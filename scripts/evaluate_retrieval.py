from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import db, knowledge_base
from app.retrieval_evaluation import evaluate_cases


def indexed_corpus_stats(connection: Any) -> dict[str, int]:
    """Return counts for sources and chunks actually available to retrieval."""
    sources = connection.execute(
        "SELECT COUNT(*) FROM knowledge_sources WHERE status='indexed'"
    ).fetchone()[0]
    chunks = connection.execute(
        """SELECT COUNT(*) FROM knowledge_chunks c
           JOIN knowledge_sources s ON s.id=c.source_id
           WHERE s.status='indexed'"""
    ).fetchone()[0]
    return {"indexed_sources": int(sources), "indexed_chunks": int(chunks)}


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
    with db.learning_connection() as connection:
        corpus = indexed_corpus_stats(connection)
    if corpus["indexed_sources"] == 0 or corpus["indexed_chunks"] == 0:
        raise SystemExit(
            "Retrieval benchmark requires an indexed corpus; "
            "index the dataset's relevant source URLs before measuring Recall@k/MRR."
        )
    report = evaluate_cases(cases, knowledge_base.search, k=args.k)
    report["dataset"] = str(dataset_path)
    report["corpus"] = corpus
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
