"""Human-adjudicated evaluation for the provisional exact-match Oracle policy.

This measures agreement with supplied labels; it does not itself establish scientific
truth. The dataset loader requires independent human adjudication and source hashes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from .learning_prompt_registry import PROMPT_REGISTRY_VERSION, prompt_version

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
        (item["oracle_id"], item["version"]): item
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
        if source_url != registered_source["source_url"]:
            raise OracleEvaluationError(f"{prefix}.source_url does not match the versioned Oracle catalog.")
        source_snapshot = _nonempty(case.get("source_snapshot"), f"{prefix}.source_snapshot")
        expected_source_hash = hashlib.sha256(source_snapshot.encode("utf-8")).hexdigest()
        if not isinstance(case.get("source_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", case["source_sha256"]) is None:
            raise OracleEvaluationError(f"{prefix}.source_sha256 must be a lowercase SHA-256 digest.")
        if case["source_sha256"] != expected_source_hash:
            raise OracleEvaluationError(f"{prefix}.source_sha256 does not match source_snapshot.")
        # The page snapshot hash is retained as human-adjudication provenance, while
        # this second fingerprint binds the dataset to the exact source summary
        # shipped with the registered Oracle version. A well-formed arbitrary hash
        # must not be mistaken for a matching catalog record.
        if case.get("source_summary_sha256") != registered_source["source_summary_sha256"]:
            raise OracleEvaluationError(
                f"{prefix}.source_summary_sha256 does not match the versioned Oracle catalog."
            )
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict):
            raise OracleEvaluationError(f"{prefix}.adjudication is required.")
        _nonempty(adjudication.get("reviewer"), f"{prefix}.adjudication.reviewer")
        _nonempty(adjudication.get("rationale"), f"{prefix}.adjudication.rationale")
        timestamp = _nonempty(adjudication.get("adjudicated_at"), f"{prefix}.adjudication.adjudicated_at")
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OracleEvaluationError(f"{prefix}.adjudication.adjudicated_at must be ISO-8601.") from exc
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise OracleEvaluationError(
                f"{prefix}.adjudication.adjudicated_at must include an explicit timezone."
            )
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

    def new_counts() -> dict[str, int]:
        return {
            "true_match": 0,
            "false_match": 0,
            "true_nonmatch": 0,
            "false_nonmatch": 0,
        }

    def record(counts: dict[str, int], expected: str, predicted: str) -> None:
        if expected == "match" and predicted == "match":
            counts["true_match"] += 1
        elif expected == "no_match" and predicted == "match":
            counts["false_match"] += 1
        elif expected == "no_match" and predicted == "no_match":
            counts["true_nonmatch"] += 1
        else:
            counts["false_nonmatch"] += 1

    def metrics(counts: dict[str, int]) -> dict[str, float | None]:
        tp, fp = counts["true_match"], counts["false_match"]
        tn, fn = counts["true_nonmatch"], counts["false_nonmatch"]
        total = tp + fp + tn + fn
        return {
            "accuracy": _rate(tp + tn, total),
            "precision": _rate(tp, tp + fp),
            "recall": _rate(tp, tp + fn),
            "false_acceptance_rate": _rate(fp, fp + tn),
            "false_rejection_rate": _rate(fn, fn + tp),
        }

    aggregate = new_counts()
    by_oracle: dict[str, dict[str, Any]] = {}
    per_case = []
    for case in checked:
        oracle_key = f"{case['oracle_id']}@{case['oracle_version']}"
        result = evaluate_candidate_claim(
            case["content"], oracle_id=case["oracle_id"],
            oracle_version=case["oracle_version"], claim=case["claim"],
        )
        predicted = "match" if result.get("status") == "matched" else "no_match"
        expected = case["expected_decision"]
        record(aggregate, expected, predicted)
        oracle_report = by_oracle.setdefault(
            oracle_key, {"oracle_id": case["oracle_id"], "oracle_version": case["oracle_version"],
                         "case_count": 0, "confusion_matrix": new_counts()},
        )
        oracle_report["case_count"] += 1
        record(oracle_report["confusion_matrix"], expected, predicted)
        per_case.append({
            "id": case["id"],
            "oracle_id": case["oracle_id"],
            "oracle_version": case["oracle_version"],
            "expected_decision": expected,
            "predicted_decision": predicted,
            "oracle_status": result.get("status"),
            "oracle_reason": result.get("reason"),
            "correct": expected == predicted,
        })

    for oracle_report in by_oracle.values():
        oracle_report["metrics"] = metrics(oracle_report["confusion_matrix"])
    return {
        "evaluator_version": prompt_version("oracle_evaluation"),
        "prompt_registry_version": PROMPT_REGISTRY_VERSION,
        "case_count": len(per_case),
        "confusion_matrix": aggregate,
        "metrics": metrics(aggregate),
        "per_oracle": by_oracle,
        "per_case": per_case,
        "interpretation": (
            "Agreement with the supplied independent human labels for the exact-match policy only; "
            "not a domain-wide scientific factuality score, semantic entailment test, or promotion signal."
        ),
    }
