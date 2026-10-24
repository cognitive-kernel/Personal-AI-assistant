"""Human-adjudicated evaluation for the provisional exact-match Oracle policy.

This measures agreement with supplied labels; it does not itself establish scientific
truth. The dataset loader requires independent human adjudication and source hashes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .learning_oracles import evaluate_candidate_claim, list_oracles


class OracleEvaluationError(ValueError):
    """Oracle evaluation data is incomplete, unsafe, or ambiguous."""


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OracleEvaluationError(f"{field} must be a non-empty string.")
    return value.strip()


def validate_oracle_dataset(dataset: Any) -> list[dict[str, Any]]:
    if not isinstance(dataset, dict) or type(dataset.get("schema_version")) is not int or dataset["schema_version"] != 1:
        raise OracleEvaluationError("schema_version must be 1.")
    _nonempty(dataset.get("dataset_name"), "dataset_name")
    _nonempty(dataset.get("dataset_version"), "dataset_version")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise OracleEvaluationError("cases must be a non-empty list.")
    seen: set[str] = set()
    catalog_sources = {
        (item["oracle_id"], item["version"]): item["source_url"]
        for item in list_oracles()
    }
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            raise OracleEvaluationError(f"{prefix} must be an object.")
        case_id = _nonempty(case.get("id"), f"{prefix}.id")
        if case_id in seen:
            raise OracleEvaluationError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        _nonempty(case.get("content"), f"{prefix}.content")
        _nonempty(case.get("claim"), f"{prefix}.claim")
        _nonempty(case.get("oracle_id"), f"{prefix}.oracle_id")
        if type(case.get("oracle_version")) is not int or case["oracle_version"] < 1:
            raise OracleEvaluationError(f"{prefix}.oracle_version must be a positive integer.")
        if case.get("expected_decision") not in {"match", "no_match"}:
            raise OracleEvaluationError(f"{prefix}.expected_decision must be match or no_match.")
        source_url = _nonempty(case.get("source_url"), f"{prefix}.source_url")
        if not source_url.startswith("https://"):
            raise OracleEvaluationError(f"{prefix}.source_url must use HTTPS.")
        registered_source = catalog_sources.get((case["oracle_id"], case["oracle_version"]))
        if registered_source is None:
            raise OracleEvaluationError(f"{prefix} references an unknown Oracle version.")
        if source_url != registered_source:
            raise OracleEvaluationError(f"{prefix}.source_url does not match the versioned Oracle catalog.")
        if not isinstance(case.get("source_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", case["source_sha256"]) is None:
            raise OracleEvaluationError(f"{prefix}.source_sha256 must be a lowercase SHA-256 digest.")
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict):
            raise OracleEvaluationError(f"{prefix}.adjudication is required.")
        _nonempty(adjudication.get("reviewer"), f"{prefix}.adjudication.reviewer")
        _nonempty(adjudication.get("rationale"), f"{prefix}.adjudication.rationale")
        timestamp = _nonempty(adjudication.get("adjudicated_at"), f"{prefix}.adjudication.adjudicated_at")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OracleEvaluationError(f"{prefix}.adjudication.adjudicated_at must be ISO-8601.") from exc
        if adjudication.get("independent_of_model") is not True:
            raise OracleEvaluationError(f"{prefix}.adjudication.independent_of_model must be true.")
    return cases


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def evaluate_oracle_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate Oracle accept/no-match decisions against independently adjudicated labels."""
    checked = validate_oracle_dataset({
        "schema_version": 1,
        "dataset_name": "in-memory",
        "dataset_version": "in-memory",
        "cases": cases,
    })
    true_match = false_match = true_nonmatch = false_nonmatch = 0
    per_case = []
    for case in checked:
        result = evaluate_candidate_claim(
            case["content"], oracle_id=case["oracle_id"],
            oracle_version=case["oracle_version"], claim=case["claim"],
        )
        predicted = "match" if result.get("status") == "matched" else "no_match"
        expected = case["expected_decision"]
        if expected == "match" and predicted == "match":
            true_match += 1
        elif expected == "no_match" and predicted == "match":
            false_match += 1
        elif expected == "no_match" and predicted == "no_match":
            true_nonmatch += 1
        else:
            false_nonmatch += 1
        per_case.append({
            "id": case["id"],
            "expected_decision": expected,
            "predicted_decision": predicted,
            "oracle_status": result.get("status"),
            "oracle_reason": result.get("reason"),
            "correct": expected == predicted,
        })
    count = len(per_case)
    return {
        "case_count": count,
        "confusion_matrix": {
            "true_match": true_match,
            "false_match": false_match,
            "true_nonmatch": true_nonmatch,
            "false_nonmatch": false_nonmatch,
        },
        "metrics": {
            "accuracy": _rate(true_match + true_nonmatch, count),
            "precision": _rate(true_match, true_match + false_match),
            "recall": _rate(true_match, true_match + false_nonmatch),
            "false_acceptance_rate": _rate(false_match, false_match + true_nonmatch),
            "false_rejection_rate": _rate(false_nonmatch, false_nonmatch + true_match),
        },
        "per_case": per_case,
        "interpretation": (
            "Agreement with the supplied independent human labels for the exact-match policy only; "
            "not a domain-wide scientific factuality score, semantic entailment test, or promotion signal."
        ),
    }
