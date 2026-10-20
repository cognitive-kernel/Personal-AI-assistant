#!/usr/bin/env python3
"""Evaluate the transfer-question gate against a versioned bilingual regression set.

This checks marker-classifier behavior only; it does not prove a question truly
measures transfer or that a learner has acquired the concept.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.learning import _has_transfer_question

DEFAULT_DATASET = ROOT / "tests" / "fixtures" / "learning_transfer_eval_v1.json"


def evaluate_dataset(dataset_path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise ValueError("Unsupported transfer benchmark schema.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Transfer benchmark must contain cases.")
    results = []
    for case in cases:
        if (
            not isinstance(case, dict) or not isinstance(case.get("id"), str)
            or not isinstance(case.get("text"), str)
            or not isinstance(case.get("expected_transfer"), bool)
        ):
            raise ValueError("Transfer benchmark case shape is invalid.")
        predicted = _has_transfer_question(case["text"])
        results.append({
            "case_id": case["id"],
            "expected_transfer": case["expected_transfer"],
            "predicted_transfer": predicted,
            "regression_passed": predicted is case["expected_transfer"],
        })
    passed = sum(item["regression_passed"] for item in results)
    return {
        "schema_version": 1,
        "benchmark_id": dataset.get("benchmark_id"),
        "case_count": len(results),
        "passed_cases": passed,
        "accuracy_percent": round(100 * passed / len(results), 2),
        "status": "passed" if passed == len(results) else "failed",
        "cases": results,
        "interpretation": (
            "Lexical marker regression only. A passing result does not prove semantic transfer, "
            "scientific correctness, or actual learner mastery."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_dataset(args.dataset)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
