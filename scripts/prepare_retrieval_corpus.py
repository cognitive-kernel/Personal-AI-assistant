from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import db, knowledge_base
from app.retrieval_evaluation import (
    indexed_corpus_stats,
    missing_indexed_relevant_sources,
)


def load_cases(dataset_path: Path) -> list[dict[str, Any]]:
    try:
        cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read evaluation dataset: {exc}") from exc
    if not isinstance(cases, list):
        raise ValueError("Evaluation dataset must be a JSON array.")
    return cases


def relevant_urls(cases: list[dict[str, Any]]) -> list[str]:
    urls: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Case {index} must be an object.")
        relevant = case.get("relevant_ids")
        if (
            not isinstance(relevant, list)
            or not relevant
            or any(not isinstance(value, str) or not value.strip() for value in relevant)
        ):
            raise ValueError(
                f"Case {index} must contain non-empty string IDs in relevant_ids."
            )
        urls.update(value.strip() for value in relevant)
    if not urls:
        raise ValueError("Evaluation dataset must contain at least one relevant source URL.")
    return sorted(urls)


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
        cases = load_cases(Path(args.dataset))
        urls = relevant_urls(cases)
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
