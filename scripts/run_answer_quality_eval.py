#!/usr/bin/env python3
"""Run a local, auditable Persian/English answer-quality diagnostic.

The run uses the configured local Ollama model and the normal research pipeline.
Its lexical checks are only screening signals, not semantic quality scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import chat_research, db, ollama
from app.answer_quality_dataset import DEFAULT_DATASET, DATASET_VERSION, load_cases
from app.answer_quality_evaluation import evaluate_answer_surface, summarize_surface_results
from app.db import get_settings, learning_connection


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _audit_sources(question: str) -> list[dict[str, Any]]:
    """Read only source provenance metadata for the exact question hash."""
    query_hash = _sha256_bytes(question[:12000].encode("utf-8"))
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT sources_json FROM chat_research_runs
               WHERE query_sha256=? ORDER BY id DESC LIMIT 1""",
            (query_hash,),
        ).fetchone()
    if row is None:
        return []
    try:
        sources = json.loads(row["sources_json"])
    except (TypeError, ValueError):
        return []
    return sources if isinstance(sources, list) else []


def run_evaluation(
    *,
    cases: list[dict[str, Any]],
    answer_fn: Callable[[str], str],
    dataset_path: Path,
    redact_answers: bool = False,
) -> dict[str, Any]:
    """Execute each case once and produce a versioned diagnostic report."""
    raw_results: list[dict[str, Any]] = []
    for case in cases:
        entry: dict[str, Any] = {"case_id": case["id"], "language": case["language"]}
        try:
            answer = answer_fn(case["prompt"])
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("assistant returned an empty/non-string answer")
            sources = _audit_sources(case["prompt"])
            entry.update(evaluate_answer_surface(case, answer, sources))
            if not redact_answers:
                entry["answer"] = answer
            entry["generation_status"] = "completed"
        except Exception as exc:  # Report failures per case; never silently drop them.
            entry.update({
                "generation_status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            })
        raw_results.append(entry)

    completed = [item for item in raw_results if item["generation_status"] == "completed"]
    settings = get_settings()
    model_name = str(settings.get("ollama_model", "unknown"))
    try:
        inventory = ollama.list_models()
    except Exception as exc:
        inventory = []
        model_inventory_error = type(exc).__name__
    else:
        model_inventory_error = None
    matching_model = next((item for item in inventory if item.get("name") == model_name), None)
    source_snapshots = [
        {
            "case_id": item["case_id"],
            "sources": [
                {
                    "url": source.get("url"),
                    "title": source.get("title"),
                    "content_sha256": source.get("content_sha256"),
                    "excerpt_chars": source.get("excerpt_chars"),
                }
                for source in _audit_sources(next(
                    case["prompt"] for case in cases if case["id"] == item["case_id"]
                ))
                if isinstance(source, dict)
            ],
        }
        for item in completed
    ]
    report: dict[str, Any] = {
        "report_schema_version": 1,
        "benchmark": "bilingual-answer-quality",
        "dataset_version": DATASET_VERSION,
        "dataset_path": str(dataset_path.relative_to(ROOT)) if dataset_path.is_relative_to(ROOT) else dataset_path.name,
        "dataset_sha256": _file_sha256(dataset_path),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "code_commit": _git_commit(),
        "prompt_code_sha256": {
            "chat_research.py": _file_sha256(ROOT / "app" / "chat_research.py"),
            "claim_review.py": _file_sha256(ROOT / "app" / "claim_review.py"),
        },
        "model": {
            "configured_name": model_name,
            "matching_local_inventory_entry": matching_model,
            "inventory_error": model_inventory_error,
            "warning": "Ollama tags expose name/size/modified time, not a cryptographic model digest; this is not an immutable model pin.",
        },
        "runtime": {
            "pipeline": "app.chat_research.answer",
            "retrieval_mode": "live source discovery; exact per-case URL/title/excerpt hashes are recorded when audit metadata exists",
            "isolated_database": True,
            "external_source_snapshot_fully_replayable": False,
        },
        "run_status": "completed" if len(completed) == len(cases) else "incomplete",
        "case_count": len(cases),
        "completed_count": len(completed),
        "failed_count": len(cases) - len(completed),
        "surface_diagnostics": summarize_surface_results([
            {key: value for key, value in item.items() if key in {
                "case_id", "language", "answer_chars", "expected_concepts_count",
                "surface_matched_concepts", "surface_missing_concepts",
                "surface_concept_coverage", "prohibited_phrase_hits",
                "expected_language_script_present", "source_urls", "source_count", "interpretation"
            }}
            for item in completed
        ]) if completed else None,
        "source_snapshots": source_snapshots,
        "results": raw_results,
        "interpretation": (
            "Surface diagnostics only. They do not establish factual correctness, source reliability, "
            "or semantic equivalence. Review every output manually; do not present these metrics as a model-quality score."
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=Path("answer-quality-report.json"))
    parser.add_argument("--redact-answers", action="store_true",
                        help="omit raw model answers from the report")
    args = parser.parse_args()
    try:
        cases = load_cases(args.dataset)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    original_settings_path = db.defaults.settings_db_path
    original_learning_path = db.defaults.learning_db_path
    try:
        with tempfile.TemporaryDirectory(prefix="answer-quality-eval-") as temp_dir:
            db.defaults.settings_db_path = str(Path(temp_dir) / "settings.db")
            db.defaults.learning_db_path = str(Path(temp_dir) / "learning.db")
            db.init_databases()
            report = run_evaluation(
                cases=cases,
                answer_fn=chat_research.answer,
                dataset_path=args.dataset.resolve(),
                redact_answers=args.redact_answers,
            )
    finally:
        db.defaults.settings_db_path = original_settings_path
        db.defaults.learning_db_path = original_learning_path

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "run_status": report["run_status"],
        "completed_count": report["completed_count"],
        "failed_count": report["failed_count"],
        "surface_diagnostics": report["surface_diagnostics"],
        "warning": report["interpretation"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
