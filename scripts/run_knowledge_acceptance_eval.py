#!/usr/bin/env python3
"""Evaluate claim-to-test mapping acceptance against versioned reference scenarios.

This is a deterministic policy benchmark. It measures false acceptance of incomplete
coverage mappings, not factual hallucination or the semantic quality of the tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "knowledge_acceptance" / "v1" / "cases.jsonl"
DATASET_VERSION = "v1"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_cases(path: str | Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"reference dataset missing: {path}")
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON constant: {value}")),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid reference case at {path}:{line_no}: {exc}") from exc
        required = {"id", "dataset_version", "domain", "claim_ids", "test_plan", "expected_decision"}
        if not isinstance(case, dict) or required - case.keys():
            raise ValueError(f"reference case at line {line_no} has an invalid schema")
        if case["dataset_version"] != DATASET_VERSION:
            raise ValueError(f"unsupported dataset version at line {line_no}")
        if not isinstance(case["id"], str) or not case["id"].strip() or case["id"] in ids:
            raise ValueError(f"empty or duplicate case id at line {line_no}")
        if not isinstance(case["domain"], str) or not case["domain"].strip():
            raise ValueError(f"missing domain at line {line_no}")
        if case["expected_decision"] not in {"accept", "reject"}:
            raise ValueError(f"invalid expected_decision at line {line_no}")
        if not isinstance(case["claim_ids"], list) or not isinstance(case["test_plan"], list):
            raise ValueError(f"claim_ids and test_plan must be lists at line {line_no}")
        ids.add(case["id"])
        cases.append(case)
    if not cases:
        raise ValueError("reference dataset is empty")
    domains = {case["domain"] for case in cases}
    if len(domains) < 3:
        raise ValueError("reference dataset must cover at least three domains")
    outcomes = {case["expected_decision"] for case in cases}
    if outcomes != {"accept", "reject"}:
        raise ValueError("reference dataset must contain both accept and reject labels")
    return cases


def evaluate_dataset(path: str | Path = DEFAULT_DATASET) -> dict[str, Any]:
    from app.claim_test_coverage import evaluate_claim_test_coverage

    cases = load_cases(path)
    results = []
    for case in cases:
        coverage = evaluate_claim_test_coverage(case["claim_ids"], case["test_plan"])
        actual = "accept" if coverage["complete"] else "reject"
        results.append({
            "case_id": case["id"], "domain": case["domain"],
            "expected_decision": case["expected_decision"], "actual_decision": actual,
            "passed": actual == case["expected_decision"], "coverage": coverage,
        })
    negative = [item for item in results if item["expected_decision"] == "reject"]
    positive = [item for item in results if item["expected_decision"] == "accept"]
    false_accepts = [item["case_id"] for item in negative if item["actual_decision"] == "accept"]
    false_rejections = [item["case_id"] for item in positive if item["actual_decision"] == "reject"]
    dataset_path = Path(path)
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    failed = [item["case_id"] for item in results if not item["passed"]]
    return {
        "benchmark_id": "claim-test-coverage-v1",
        "dataset_version": DATASET_VERSION,
        "dataset_path": str(dataset_path),
        "dataset_sha256": dataset_sha256,
        "case_count": len(results),
        "domain_count": len({item["domain"] for item in results}),
        "passed_cases": len(results) - len(failed),
        "failed_cases": failed,
        "status": "passed" if not failed else "failed",
        "confusion": {
            "reference_accept_count": len(positive),
            "reference_reject_count": len(negative),
            "false_accept_count": len(false_accepts),
            "false_acceptance_rate": round(len(false_accepts) / len(negative), 4) if negative else None,
            "false_acceptance_case_ids": false_accepts,
            "false_reject_count": len(false_rejections),
            "false_rejection_rate": round(len(false_rejections) / len(positive), 4) if positive else None,
            "false_rejection_case_ids": false_rejections,
        },
        "domains": sorted({item["domain"] for item in results}),
        "human_scoring_used": False,
        "independent_semantic_test_review": False,
        "interpretation": (
            "Deterministic structural-coverage policy benchmark only. It does not establish that mapped "
            "tests are semantically adequate or that the candidate claims are true. CI success is not live-model "
            "or target-host acceptance."
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_dataset(args.dataset)
    except ValueError as exc:
        parser.error(str(exc))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"results"}}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" and report["confusion"]["false_acceptance_rate"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
