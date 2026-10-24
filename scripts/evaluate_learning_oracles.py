#!/usr/bin/env python3
"""Run the provisional Oracle policy against an independently human-adjudicated dataset."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.learning_oracle_evaluation import (  # noqa: E402
    OracleEvaluationError,
    evaluate_oracle_cases,
    validate_oracle_dataset,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        help="JSON dataset with independent human adjudication, page snapshot hashes, and the registered source-summary fingerprint",
    )
    parser.add_argument("--max-false-acceptance-rate", type=float, default=None)
    parser.add_argument("--min-precision", type=float, default=None)
    args = parser.parse_args(argv)
    if args.max_false_acceptance_rate is not None and not 0 <= args.max_false_acceptance_rate <= 1:
        parser.error("--max-false-acceptance-rate must be between 0 and 1")
    if args.min_precision is not None and not 0 <= args.min_precision <= 1:
        parser.error("--min-precision must be between 0 and 1")
    try:
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        cases = validate_oracle_dataset(dataset)
        report = evaluate_oracle_cases(cases)
    except (OSError, json.JSONDecodeError, OracleEvaluationError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    report["dataset_name"] = dataset["dataset_name"]
    report["dataset_version"] = dataset["dataset_version"]
    report["oracle_catalog_version"] = 1
    report["dataset_adjudication"] = "independent_human_required"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    metrics = report["metrics"]
    failed = False
    if args.max_false_acceptance_rate is not None:
        value = metrics["false_acceptance_rate"]
        failed = failed or value is None or value > args.max_false_acceptance_rate
    if args.min_precision is not None:
        value = metrics["precision"]
        failed = failed or value is None or value < args.min_precision
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
