#!/usr/bin/env python3
"""Evaluate learning acceptance decisions against a human-adjudicated JSON dataset.

This tool measures a supplied evaluation run; it does not call an LLM, decide truth,
or turn model self-review into independent evidence. Keep datasets versioned and
store only cases whose expected decision has been adjudicated by a human.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

DECISIONS = {"accept", "reject"}
DATASET_SCHEMA_VERSION = 1


class EvaluationDataError(ValueError):
    """Raised when an evaluation dataset is incomplete or ambiguous."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDataError(f"{field} must be a non-empty string")
    return value.strip()


def load_dataset(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"cannot read valid JSON dataset: {exc}") from exc
    if not isinstance(data, dict):
        raise EvaluationDataError("dataset root must be a JSON object")
    if data.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise EvaluationDataError(f"schema_version must be {DATASET_SCHEMA_VERSION}")
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
        for field in ("domain", "language", "expected_decision", "observed_decision"):
            _non_empty_string(case.get(field), f"{prefix}.{field}")
        if case["expected_decision"] not in DECISIONS:
            raise EvaluationDataError(f"{prefix}.expected_decision must be accept or reject")
        if case["observed_decision"] not in DECISIONS:
            raise EvaluationDataError(f"{prefix}.observed_decision must be accept or reject")
        if not isinstance(case.get("source_refs"), list) or any(
            not isinstance(ref, str) or not ref.strip() for ref in case["source_refs"]
        ):
            raise EvaluationDataError(f"{prefix}.source_refs must be a list of non-empty strings")
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict):
            raise EvaluationDataError(f"{prefix}.adjudication is required")
        _non_empty_string(adjudication.get("reviewer"), f"{prefix}.adjudication.reviewer")
        _non_empty_string(adjudication.get("adjudicated_at"), f"{prefix}.adjudication.adjudicated_at")
        _non_empty_string(adjudication.get("rationale"), f"{prefix}.adjudication.rationale")
        if adjudication.get("independent_of_model") is not True:
            raise EvaluationDataError(
                f"{prefix}.adjudication.independent_of_model must be true"
            )
    return data


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    per_case = []
    for case in cases:
        expected = case["expected_decision"]
        observed = case["observed_decision"]
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
    parser.add_argument("dataset", help="path to a versioned, human-adjudicated JSON dataset")
    parser.add_argument("--max-false-acceptance-rate", type=float, default=None)
    parser.add_argument("--max-false-rejection-rate", type=float, default=None)
    args = parser.parse_args(argv)
    for name in ("max_false_acceptance_rate", "max_false_rejection_rate"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    try:
        dataset = load_dataset(args.dataset)
    except EvaluationDataError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    report = evaluate(dataset["cases"])
    report["dataset_version"] = dataset["dataset_version"]
    report["dataset_name"] = dataset.get("dataset_name", "")
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
