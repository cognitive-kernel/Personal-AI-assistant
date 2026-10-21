#!/usr/bin/env python3
"""Run a local, auditable Persian/English answer-quality diagnostic.

The run uses the configured local Ollama model and the normal research pipeline.
Its lexical checks are only screening signals, not semantic quality scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import chat_research, db, ollama
from app.answer_quality_dataset import DEFAULT_DATASET, DATASET_VERSION, load_cases
from app.answer_quality_evaluation import (
    evaluate_answer_surface,
    evaluate_automated_acceptance,
    summarize_surface_results,
)
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


def _audit_diagnostics(question: str) -> dict[str, Any]:
    """Read bounded source-discovery/review status for the exact question hash."""
    query_hash = _sha256_bytes(question[:12000].encode("utf-8"))
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT source_count, review_status, review_summary_json
               FROM chat_research_runs
               WHERE query_sha256=? ORDER BY id DESC LIMIT 1""",
            (query_hash,),
        ).fetchone()
    if row is None:
        return {"research_review_status": "missing"}
    result: dict[str, Any] = {
        "research_review_status": str(row["review_status"]),
        "audited_source_count": int(row["source_count"]),
    }
    try:
        review = json.loads(row["review_summary_json"] or "{}")
    except (TypeError, ValueError):
        review = {}
    error = review.get("error") if isinstance(review, dict) else None
    if isinstance(error, str) and error.startswith("source_discovery_failed:"):
        result["source_discovery_status"] = "failed"
        result["source_discovery_error_type"] = error.split(":", 1)[1][:80]
    elif isinstance(error, str) and error == "no_usable_sources":
        result["source_discovery_status"] = "no_usable_sources"
    elif int(row["source_count"]) > 0:
        result["source_discovery_status"] = "sources_found"
    else:
        result["source_discovery_status"] = "no_sources"
    return result


def run_evaluation(
    *,
    cases: list[dict[str, Any]],
    answer_fn: Callable[[str], str],
    dataset_path: Path,
    redact_answers: bool = False,
    max_case_seconds: float = 180.0,
) -> dict[str, Any]:
    """Execute each case once and produce a versioned diagnostic report."""
    raw_results: list[dict[str, Any]] = []
    sources_by_case_id: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        entry: dict[str, Any] = {"case_id": case["id"], "language": case["language"]}
        started = time.perf_counter()
        try:
            answer = answer_fn(case["prompt"])
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("assistant returned an empty/non-string answer")
        except Exception as exc:  # Keep error details out of potentially shared reports.
            entry.update({
                "generation_seconds": round(time.perf_counter() - started, 4),
                "generation_status": "failed",
                "generation_error_type": type(exc).__name__,
            })
            raw_results.append(entry)
            continue

        # Generation success is independent of optional provenance and lexical diagnostics.
        entry["generation_seconds"] = round(time.perf_counter() - started, 4)
        entry["generation_status"] = "completed"
        if not redact_answers:
            entry["answer"] = answer
        try:
            sources = _audit_sources(case["prompt"])
        except Exception as exc:
            sources = []
            entry.update({
                "source_audit_status": "unavailable",
                "source_audit_error_type": type(exc).__name__,
            })
        else:
            entry["source_audit_status"] = "completed"
        sources_by_case_id[case["id"]] = sources
        try:
            entry.update(_audit_diagnostics(case["prompt"]))
        except Exception as exc:
            entry["research_audit_status"] = "unavailable"
            entry["research_audit_error_type"] = type(exc).__name__

        try:
            entry.update(evaluate_answer_surface(case, answer, sources))
        except Exception as exc:
            entry.update({
                "surface_diagnostic_status": "failed",
                "surface_diagnostic_error_type": type(exc).__name__,
            })
        else:
            entry["surface_diagnostic_status"] = "completed"
        raw_results.append(entry)

    completed = [item for item in raw_results if item["generation_status"] == "completed"]
    diagnosed = [item for item in completed if item.get("surface_diagnostic_status") == "completed"]
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
                for source in sources_by_case_id.get(item["case_id"], [])
                if isinstance(source, dict)
            ],
        }
        for item in completed
    ]
    automated_acceptance = evaluate_automated_acceptance(
        raw_results, maximum_generation_seconds=max_case_seconds
    )
    report: dict[str, Any] = {
        "report_schema_version": 2,
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
            "manifest_digest": matching_model.get("digest") if isinstance(matching_model, dict) else None,
            "inventory_error": model_inventory_error,
            "warning": (
                None if isinstance(matching_model, dict) and matching_model.get("digest")
                else "No model manifest digest was available; name/size/modified time are not an immutable model pin."
            ),
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
        "generation_completed_count": len(completed),
        "generation_failed_count": len(cases) - len(completed),
        "performance": {
            "generation_seconds_total": round(sum(float(item.get("generation_seconds", 0)) for item in raw_results), 4),
            "generation_seconds_max": round(max((float(item.get("generation_seconds", 0)) for item in raw_results), default=0.0), 4),
            "configured_max_generation_seconds_per_case": automated_acceptance["maximum_generation_seconds"],
            "interpretation": "Measured on the machine running this report; hardware and model dependent.",
        },
        "quality_assessment_status": "automated_gate_passed" if automated_acceptance["passed"] else "automated_gate_failed",
        "automated_acceptance": automated_acceptance,
        "surface_diagnostics": summarize_surface_results([
            {key: value for key, value in item.items() if key in {
                "case_id", "language", "answer_chars", "expected_concepts_count",
                "surface_matched_concepts", "surface_missing_concepts",
                "surface_concept_coverage", "prohibited_phrase_hits",
                "expected_language_script_present", "source_urls", "source_count", "answer_urls",
                "unattributed_answer_urls", "unattributed_answer_url_count", "interpretation"
            }}
            for item in diagnosed
        ]) if diagnosed else None,
        "surface_diagnostic_completed_count": len(diagnosed),
        "surface_diagnostic_failed_count": len(completed) - len(diagnosed),
        "source_snapshots": source_snapshots,
        "results": raw_results,
        "interpretation": (
            "The automated gate uses versioned reference concepts, prohibited-claim checks, source provenance, "
            "and model-assisted claim-review status. It does not use human scoring and is not proof of scientific truth. "
            "Lexical checks can miss paraphrases; a passing result is a bounded regression signal only."
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=Path("answer-quality-report.json"))
    parser.add_argument("--max-case-seconds", type=float, default=180.0,
                        help="maximum permitted generation time per benchmark case (default: 180 seconds)")
    parser.add_argument("--redact-answers", action="store_true",
                        help="omit raw model answers from the report")
    args = parser.parse_args()
    if not math.isfinite(args.max_case_seconds) or args.max_case_seconds <= 0:
        parser.error("--max-case-seconds must be a finite positive number")
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
                max_case_seconds=args.max_case_seconds,
            )
            report.setdefault("performance", {})["configured_max_generation_seconds_per_case"] = args.max_case_seconds
    finally:
        db.defaults.settings_db_path = original_settings_path
        db.defaults.learning_db_path = original_learning_path

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "run_status": report["run_status"],
        "completed_count": report["generation_completed_count"],
        "generation_failed_count": report["generation_failed_count"],
        "quality_assessment_status": report["quality_assessment_status"],
        "automated_acceptance": report["automated_acceptance"],
        "surface_diagnostics": report["surface_diagnostics"],
        "warning": report["interpretation"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["run_status"] == "completed" and report["automated_acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
