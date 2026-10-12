from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import db, knowledge_base
from app.retrieval_evaluation import evaluate_cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate local knowledge retrieval against labeled queries.")
    parser.add_argument("--dataset", default="tests/fixtures/retrieval_eval.json")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    dataset_path = Path(args.dataset)
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("Evaluation dataset must be a JSON array.")
    db.init_databases()
    report = evaluate_cases(cases, knowledge_base.search, k=args.k)
    report["dataset"] = str(dataset_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
