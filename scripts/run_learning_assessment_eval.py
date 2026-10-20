#!/usr/bin/env python3
"""Run the versioned deterministic rubric-scoring regression benchmark.

This evaluates scorer implementation against known cases; it does not measure
model-generated rubric quality or prove semantic understanding.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.assessment_rubric import normalize_rubric, score_answers

DEFAULT_DATASET = ROOT / "tests" / "fixtures" / "learning_assessment_eval_v1.json"


def evaluate_dataset(dataset_path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise ValueError("Unsupported learning assessment benchmark schema.")
    cases = dataset.get("cases")
    threshold = dataset.get("mastery_threshold")
    if (
        not isinstance(cases, list) or not cases
        or isinstance(threshold, bool) or not isinstance(threshold, (int, float))
        or not 0 < threshold <= 100
    ):
        raise ValueError("Learning assessment benchmark metadata is invalid.")

    results = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("Benchmark case shape is invalid.")
        answers = case.get("answers")
        rubric = normalize_rubric(case.get("rubric"), len(answers) if isinstance(answers, list) else 0)
        if rubric is None or not isinstance(answers, list):
            raise ValueError(f"Benchmark case {case['id']} has an invalid rubric or answer list.")
        scored = score_answers(answers, rubric)
        score = scored["score"]
        mastery = score >= threshold
        expected_score = case.get("expected_score")
        expected_mastery = case.get("expected_mastery")
        passed = (
            isinstance(expected_score, (int, float)) and not isinstance(expected_score, bool)
            and score == float(expected_score) and isinstance(expected_mastery, bool)
            and mastery is expected_mastery
        )
        results.append({
            "case_id": case["id"],
            "score": score,
            "mastery": mastery,
            "expected_score": expected_score,
            "expected_mastery": expected_mastery,
            "regression_passed": passed,
            "missing_concepts": [
                concept
                for question in scored["question_results"]
                for concept in question["missing_concepts"]
            ],
        })

    passed_count = sum(item["regression_passed"] for item in results)
    return {
        "schema_version": 1,
        "benchmark_id": dataset.get("benchmark_id"),
        "scoring_method": "rubric_concept_coverage_v1",
        "mastery_threshold": float(threshold),
        "case_count": len(results),
        "passed_cases": passed_count,
        "regression_accuracy_percent": round(100 * passed_count / len(results), 2),
        "status": "passed" if passed_count == len(results) else "failed",
        "cases": results,
        "interpretation": (
            "This is a deterministic scorer regression benchmark only. It does not validate "
            "model-generated rubrics, semantic equivalence, scientific correctness, or actual learner mastery."
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
