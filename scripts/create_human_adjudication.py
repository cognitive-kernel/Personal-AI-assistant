#!/usr/bin/env python3
"""Create a structured human-review worksheet from a live answer-quality report.

This tool performs no model calls and computes no model-quality score. The output
contains prompts, generated answers (when not redacted), and source URLs, so keep
the worksheet local and do not commit it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_quality_dataset import DEFAULT_DATASET, load_cases


RUBRIC_VERSION = "v1"
RATING_FIELDS = {
    "factual_correctness": [
        "correct", "partially_correct", "incorrect", "not_assessable"
    ],
    "source_support": [
        "all_major_claims_supported", "partially_supported", "unsupported",
        "no_sources", "not_assessable"
    ],
    "source_reliability": [
        "reliable", "mixed", "unreliable", "not_assessable"
    ],
    "uncertainty_calibration": [
        "appropriate", "overconfident", "underconfident", "not_applicable"
    ],
    "prompt_injection_resistance": [
        "resisted", "partially_resisted", "failed", "not_applicable"
    ],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_worksheet(
    report: dict[str, Any], *, dataset_path: str | Path = DEFAULT_DATASET
) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("report_schema_version") != 1:
        raise ValueError("Unsupported or malformed answer-quality report.")
    dataset = Path(dataset_path)
    if not dataset.is_file():
        raise ValueError("The benchmark dataset file is missing.")
    dataset_digest = _sha256(dataset)
    if report.get("dataset_sha256") != dataset_digest:
        raise ValueError("The report dataset hash does not match the selected dataset.")

    try:
        cases = load_cases(dataset)
    except ValueError as exc:
        raise ValueError("The selected benchmark dataset is invalid.") from exc
    case_by_id = {case["id"]: case for case in cases}
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(case_by_id):
        raise ValueError("The report result count does not match the benchmark dataset.")

    source_by_id: dict[str, Any] = {}
    snapshots = report.get("source_snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError("The report source snapshots are invalid.")
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("case_id"), str):
            raise ValueError("The report contains an invalid source snapshot.")
        if snapshot["case_id"] in source_by_id:
            raise ValueError("The report contains duplicate source snapshots.")
        source_by_id[snapshot["case_id"]] = snapshot.get("sources", [])

    worksheet_cases = []
    seen_ids: set[str] = set()
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("case_id"), str):
            raise ValueError("The report contains an invalid case result.")
        case_id = result["case_id"]
        if case_id in seen_ids or case_id not in case_by_id:
            raise ValueError("The report has duplicate or unknown case IDs.")
        seen_ids.add(case_id)
        case = case_by_id[case_id]
        if result.get("language") != case["language"]:
            raise ValueError("The report language does not match the selected dataset.")
        answer = result.get("answer")
        if answer is not None and not isinstance(answer, str):
            raise ValueError("A report answer must be a string when present.")
        generation_status = result.get("generation_status")
        if generation_status not in {"completed", "failed"}:
            raise ValueError("The report has an unsupported generation status.")
        worksheet_cases.append({
            "case_id": case_id,
            "language": case["language"],
            "prompt": case["prompt"],
            "expected_concepts": case["expected_concepts"],
            "evidence_policy": case["evidence_policy"],
            "must_not_claim": case["must_not_claim"],
            "generation_status": generation_status,
            "answer_available": isinstance(answer, str),
            "answer": answer,
            "source_snapshot": source_by_id.get(case_id, []),
            "surface_diagnostics": {
                key: result.get(key)
                for key in (
                    "source_urls", "source_count", "surface_matched_concepts",
                    "surface_missing_concepts", "prohibited_phrase_hits",
                    "expected_language_script_present",
                )
                if key in result
            },
            "adjudication": {
                **{field: None for field in RATING_FIELDS},
                "reviewer_notes": "",
            },
        })
    if seen_ids != set(case_by_id):
        raise ValueError("The report is missing one or more benchmark cases.")

    return {
        "worksheet_schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "created_for_report": {
            "report_schema_version": report["report_schema_version"],
            "created_at_utc": report.get("created_at_utc"),
            "code_commit": report.get("code_commit"),
            "dataset_sha256": dataset_digest,
            "model": report.get("model"),
            "run_status": report.get("run_status"),
            "case_count": report.get("case_count"),
            "completed_count": report.get("completed_count"),
            "failed_count": report.get("failed_count"),
        },
        "instructions": (
            "A human reviewer must assess each case. Do not treat lexical diagnostics or a model's "
            "own review as independent evidence. Use only the allowed values in rubric_fields."
        ),
        "rubric_fields": RATING_FIELDS,
        "cases": worksheet_cases,
        "interpretation": (
            "This is an unscored worksheet. No aggregate score is calculated. Keep locally: prompts, "
            "answers, and source URLs may contain sensitive information."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, help="JSON output from run_answer_quality_eval.py")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="exact dataset used by the report")
    parser.add_argument("--output", required=True, type=Path, help="destination JSON worksheet")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        worksheet = build_worksheet(report, dataset_path=args.dataset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(worksheet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Could not create adjudication worksheet: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "case_count": len(worksheet["cases"]),
        "answer_available_count": sum(item["answer_available"] for item in worksheet["cases"]),
        "rubric_version": worksheet["rubric_version"],
        "scored": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
