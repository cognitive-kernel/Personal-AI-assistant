#!/usr/bin/env python3
"""Evaluate learning acceptance decisions against a human-adjudicated JSON dataset.

The reference dataset and a model/run's observations are separate inputs so the
gold labels stay immutable across repeated evaluations. This tool does not call
an LLM, establish truth, or promote knowledge.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DECISIONS = {"accept", "reject"}
DATASET_SCHEMA_VERSION = 2
OBSERVATION_SCHEMA_VERSION = 1


class EvaluationDataError(ValueError):
    """Raised when evaluation data is incomplete or ambiguous."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDataError(f"{field} must be a non-empty string")
    return value.strip()


def _iso_timestamp(value: Any, field: str) -> str:
    timestamp = _non_empty_string(value, field)
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationDataError(f"{field} must be an ISO-8601 timestamp") from exc
    return timestamp


def _read_json(path: str | Path, description: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"cannot read valid {description} JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EvaluationDataError(f"{description} root must be a JSON object")
    return data


def load_dataset(path: str | Path) -> dict[str, Any]:
    data = _read_json(path, "reference dataset")
    if type(data.get("schema_version")) is not int or data["schema_version"] != DATASET_SCHEMA_VERSION:
        raise EvaluationDataError(f"schema_version must be {DATASET_SCHEMA_VERSION}")
    _non_empty_string(data.get("dataset_name"), "dataset_name")
    _non_empty_string(data.get("dataset_version"), "dataset_version")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDataError("cases must be a non-empty list")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            raise EvaluationDataError(f"{prefix} must be an object")
        case_id = _non_empty_string(case.get("id"), f"{prefix}.id")
        if case_id in seen_ids:
            raise EvaluationDataError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        for field in ("domain", "language", "expected_decision"):
            _non_empty_string(case.get(field), f"{prefix}.{field}")
        if case["expected_decision"] not in DECISIONS:
            raise EvaluationDataError(f"{prefix}.expected_decision must be accept or reject")
        source_refs = case.get("source_refs")
        if not isinstance(source_refs, list) or any(
            not isinstance(ref, str) or not ref.strip() for ref in source_refs
        ):
            raise EvaluationDataError(f"{prefix}.source_refs must be a list of non-empty strings")
        if len(set(source_refs)) != len(source_refs):
            raise EvaluationDataError(f"{prefix}.source_refs must not contain duplicates")
        source_hashes = case.get("source_hashes")
        if not isinstance(source_hashes, dict):
            raise EvaluationDataError(f"{prefix}.source_hashes must map each source reference to a SHA-256 hash")
        if set(source_hashes) != set(source_refs):
            raise EvaluationDataError(f"{prefix}.source_hashes keys must exactly match source_refs")
        for source_ref, digest in source_hashes.items():
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise EvaluationDataError(
                    f"{prefix}.source_hashes[{source_ref!r}] must be a lowercase 64-character SHA-256 hex digest"
                )
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict):
            raise EvaluationDataError(f"{prefix}.adjudication is required")
        _non_empty_string(adjudication.get("reviewer"), f"{prefix}.adjudication.reviewer")
        _iso_timestamp(adjudication.get("adjudicated_at"), f"{prefix}.adjudication.adjudicated_at")
        _non_empty_string(adjudication.get("rationale"), f"{prefix}.adjudication.rationale")
        if adjudication.get("independent_of_model") is not True:
            raise EvaluationDataError(f"{prefix}.adjudication.independent_of_model must be true")
    return data


def load_observations(path: str | Path) -> dict[str, Any]:
    data = _read_json(path, "observation")
    if type(data.get("schema_version")) is not int or data["schema_version"] != DATASET_SCHEMA_VERSION:
        raise EvaluationDataError(f"observation schema_version must be {DATASET_SCHEMA_VERSION}")
    metadata = data.get("run_metadata")
    if not isinstance(metadata, dict):
        raise EvaluationDataError("run_metadata is required")
    for field in ("model_id", "model_digest", "prompt_version", "application_commit"):
        _non_empty_string(metadata.get(field), f"run_metadata.{field}")
    _iso_timestamp(metadata.get("evaluated_at"), "run_metadata.evaluated_at")
    observations = data.get("observations")
    if not isinstance(observations, list) or not observations:
        raise EvaluationDataError("observations must be a non-empty list")
    seen_ids: set[str] = set()
    for index, item in enumerate(observations):
        prefix = f"observations[{index}]"
        if not isinstance(item, dict):
            raise EvaluationDataError(f"{prefix} must be an object")
        case_id = _non_empty_string(item.get("id"), f"{prefix}.id")
        if case_id in seen_ids:
            raise EvaluationDataError(f"duplicate observation id: {case_id}")
        seen_ids.add(case_id)
        decision = _non_empty_string(item.get("observed_decision"), f"{prefix}.observed_decision")
        if decision not in DECISIONS:
            raise EvaluationDataError(f"{prefix}.observed_decision must be accept or reject")
    return data


def evaluate(cases: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = {case["id"] for case in cases}
    observed_by_id = {item["id"]: item["observed_decision"] for item in observations}
    observed_ids = set(observed_by_id)
    if expected_ids != observed_ids:
        missing = sorted(expected_ids - observed_ids)
        unknown = sorted(observed_ids - expected_ids)
        details = []
        if missing:
            details.append("missing observations: " + ", ".join(missing))
        if unknown:
            details.append("unknown observation IDs: " + ", ".join(unknown))
        raise EvaluationDataError("; ".join(details))

    tp = fp = tn = fn = 0
    per_case = []
    for case in cases:
        expected = case["expected_decision"]
        observed = observed_by_id[case["id"]]
        if expected == "accept" and observed == "accept":
            tp += 1
            outcome = "true_accept"
        elif expected == "reject" and observed == "accept":
            fp += 1
            outcome = "false_accept"
        elif expected == "reject" and observed == "reject":
            tn += 1
            outcome = "true_reject"
        else:
            fn += 1
            outcome = "false_reject"
        per_case.append({"id": case["id"], "outcome": outcome})
    total = tp + fp + tn + fn
    expected_accepts = tp + fn
    expected_rejects = tn + fp
    observed_accepts = tp + fp

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    precision = ratio(tp, observed_accepts)
    recall = ratio(tp, expected_accepts)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    return {
        "case_count": total,
        "confusion_matrix": {
            "true_accept": tp,
            "false_accept": fp,
            "true_reject": tn,
            "false_reject": fn,
        },
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_acceptance_rate": ratio(fp, expected_rejects),
            "false_rejection_rate": ratio(fn, expected_accepts),
            "accuracy": ratio(tp + tn, total),
        },
        "case_outcomes": per_case,
        "interpretation": (
            "Metrics describe only this adjudicated dataset; they are not a calibrated "
            "probability or proof of general knowledge quality."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="path to the versioned human-adjudicated reference JSON")
    parser.add_argument("observations", help="path to the model/run observations JSON")
    parser.add_argument("--max-false-acceptance-rate", type=float, default=None)
    parser.add_argument("--max-false-rejection-rate", type=float, default=None)
    args = parser.parse_args(argv)
    for name in ("max_false_acceptance_rate", "max_false_rejection_rate"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    try:
        dataset = load_dataset(args.dataset)
        observations = load_observations(args.observations)
        report = evaluate(dataset["cases"], observations["observations"])
    except EvaluationDataError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    report["dataset_name"] = dataset["dataset_name"]
    report["dataset_version"] = dataset["dataset_version"]
    report["run_metadata"] = observations["run_metadata"]
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    metrics = report["metrics"]
    for flag, metric in (
        (args.max_false_acceptance_rate, "false_acceptance_rate"),
        (args.max_false_rejection_rate, "false_rejection_rate"),
    ):
        if flag is not None:
            actual = metrics[metric]
            if actual is None or actual > flag:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
