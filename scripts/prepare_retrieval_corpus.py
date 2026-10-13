from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import db, knowledge_base
from app.retrieval_evaluation import (
    indexed_corpus_stats,
    load_evaluation_cases,
    missing_indexed_relevant_sources,
    relevant_source_urls,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index missing source URLs required by the retrieval evaluation dataset."
    )
    parser.add_argument("--dataset", default="tests/fixtures/retrieval_eval.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the dataset's unique relevant URLs without changing the knowledge base.",
    )
    args = parser.parse_args()

    try:
        cases = load_evaluation_cases(Path(args.dataset))
        urls = relevant_source_urls(cases)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.dry_run:
        print(json.dumps(
            {"dataset": args.dataset, "relevant_urls": urls, "dry_run": True},
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    db.init_databases()
    with db.learning_connection() as connection:
        missing_before = missing_indexed_relevant_sources(connection, cases)

    indexed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in missing_before:
        try:
            indexed.append(knowledge_base.ingest_url(url))
        except (OSError, TimeoutError, ValueError) as exc:
            errors.append({"url": url, "error": str(exc)[:300]})

    with db.learning_connection() as connection:
        corpus = indexed_corpus_stats(connection)
        missing_after = missing_indexed_relevant_sources(connection, cases)

    report = {
        "dataset": args.dataset,
        "required_urls": len(urls),
        "missing_before": missing_before,
        "ingested": indexed,
        "errors": errors,
        "missing_after": missing_after,
        "corpus": corpus,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors or missing_after else 0


if __name__ == "__main__":
    raise SystemExit(main())
