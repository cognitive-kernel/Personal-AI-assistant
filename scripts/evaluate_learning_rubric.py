#!/usr/bin/env python3
"""Evaluate the deterministic learning-rubric scorer against a fixed regression set."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.learning_rubric import score_with_rubric


FIXTURE = ROOT / "tests" / "fixtures" / "learning_rubric_calibration.json"


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    threshold = float(fixture["pass_threshold"])
    rubric = fixture["rubric"]
    rows = []
    false_passes = []
    false_fails = []
    true_positive = true_negative = 0
    for case in fixture["cases"]:
        result = score_with_rubric(case["answers"], rubric)
        predicted_pass = float(result["score"]) >= threshold
        expected_pass = case["expected_pass"] is True
        if predicted_pass and not expected_pass:
            false_passes.append(case["id"])
        elif expected_pass and not predicted_pass:
            false_fails.append(case["id"])
        elif predicted_pass:
            true_positive += 1
        else:
            true_negative += 1
        rows.append({
            "id": case["id"],
            "score": result["score"],
            "predicted_pass": predicted_pass,
            "expected_pass": expected_pass,
            "matches_expectation": predicted_pass == expected_pass,
        })
    predicted_positive = true_positive + len(false_passes)
    actual_positive = true_positive + len(false_fails)
    return {
        "schema_version": fixture["schema_version"],
        "pass_threshold": threshold,
        "case_count": len(rows),
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_passes": false_passes,
        "false_fails": false_fails,
        "precision": round(true_positive / predicted_positive, 4) if predicted_positive else 1.0,
        "recall": round(true_positive / actual_positive, 4) if actual_positive else 1.0,
        "cases": rows,
        "passed": not false_passes and not false_fails,
        "note": "Fixed deterministic regression set, not a claim of population-level calibration or proof of understanding.",
    }


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = evaluate_fixture(fixture)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
