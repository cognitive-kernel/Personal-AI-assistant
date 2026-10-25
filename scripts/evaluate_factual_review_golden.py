"""Run the live independent factual-review model against a versioned golden set.

This is a manual evaluation command; it requires an explicitly configured distinct
Ollama review model. It emits aggregate metrics and case-level decisions as JSON.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

from app.factual_semantic_review import review_claim_against_source


FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "factual_review_model_golden.json"


def expected_calibration_error(rows: list[dict], bins: int = 5) -> float | None:
    if not rows:
        return None
    total = len(rows)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        bucket = [
            row for row in rows
            if low <= row["confidence"] < high or (index == bins - 1 and row["confidence"] == 1.0)
        ]
        if not bucket:
            continue
        confidence = sum(row["confidence"] for row in bucket) / len(bucket)
        accuracy = sum(row["expected_accepted"] is row["accepted"] for row in bucket) / len(bucket)
        error += len(bucket) / total * abs(confidence - accuracy)
    return round(error, 4)


def main() -> int:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        claim_hash = hashlib.sha256(case["claim"].encode("utf-8")).hexdigest()
        source_hash = hashlib.sha256(case["source_excerpt"].encode("utf-8")).hexdigest()
        decision = review_claim_against_source(
            claim=case["claim"],
            claim_hash=claim_hash,
            source_excerpt=case["source_excerpt"],
            source_url=case["source_url"],
            source_hash=source_hash,
            source_version=case["source_version"],
            claim_version=case["claim_version"],
            environment_name=case["environment_name"],
            environment_version=case["environment_version"],
            test_source=case["test_source"],
        )
        confidence = decision.confidence
        if confidence is None or not math.isfinite(confidence):
            raise RuntimeError("Reviewer omitted a valid confidence value; calibration cannot be computed.")
        results.append({
            "id": case["id"],
            "expected_accepted": case["expected_accepted"],
            "accepted": decision.accepted,
            "correct": decision.accepted is case["expected_accepted"],
            "verdict": decision.verdict,
            "confidence": confidence,
            "rubric_version": decision.rubric_version,
            "evaluator_model": decision.evaluator_model,
            "evaluator_digest": decision.evaluator_digest,
            "reasons": list(decision.reasons),
        })

    positives = [item for item in results if item["expected_accepted"]]
    negatives = [item for item in results if not item["expected_accepted"]]
    accepted = [item for item in results if item["accepted"]]
    scored = [item for item in results if item["verdict"] != "insufficient_evidence"]
    brier = sum((item["confidence"] - float(item["expected_accepted"])) ** 2 for item in results) / len(results)
    metrics = {
        "case_count": len(results),
        "accuracy": round(sum(item["correct"] for item in results) / len(results), 4),
        "precision": round(sum(item["accepted"] for item in positives) / len(accepted), 4) if accepted else 0.0,
        "recall": round(sum(item["accepted"] for item in positives) / len(positives), 4) if positives else None,
        "false_acceptance_rate": round(sum(item["accepted"] for item in negatives) / len(negatives), 4) if negatives else None,
        "coverage": round(len(scored) / len(results), 4),
        "abstention_rate": round(sum(item["verdict"] == "insufficient_evidence" for item in results) / len(results), 4),
        "brier_score": round(brier, 4),
        "expected_calibration_error_5_bins": expected_calibration_error(results),
        "all_cases_correct": all(item["correct"] for item in results),
        "results": results,
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["all_cases_correct"] else 2


if __name__ == "__main__":
    sys.exit(main())
