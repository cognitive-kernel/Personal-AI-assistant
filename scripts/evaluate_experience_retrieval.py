#!/usr/bin/env python3
"""Run the bilingual experience-retrieval benchmark against an isolated temporary DB."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, experience, ollama
from app.db import learning_connection, get_settings
from app.experience_retrieval import search_relevant_experiences
from app.retrieval_evaluation import evaluate_ranked_queries


def _seed_experiences(dataset: dict[str, Any]) -> dict[int, str]:
    ids: dict[int, str] = {}
    for item in dataset["experiences"]:
        created = experience.create_experience(
            item["topic"],
            item["problem"],
            item.get("hypothesis", ""),
        )
        verified = experience.update_experience(
            created["id"],
            status="verified",
            source_evidence=[{
                "url": item["source_url"],
                "claim": item["claim"],
            }],
            test_plan=[{"test": "synthetic_retrieval_fixture"}],
            test_results=[{
                "test": "synthetic_retrieval_fixture",
                "passed": True,
                "actual": "seeded synthetic benchmark record",
                "runner": "retrieval-benchmark-v1",
            }],
            takeaway=item["takeaway"],
            _controlled_run=True,
        )
        ids[int(verified["id"])] = item["slug"]
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lexical-only",
        action="store_true",
        help="disable local embeddings to produce a deterministic lexical baseline",
    )
    args = parser.parse_args()
    if args.lexical_only:
        def _embeddings_disabled(*_args, **_kwargs):
            raise ollama.OllamaError("disabled by --lexical-only")
        ollama.embed = _embeddings_disabled

    dataset_path = ROOT / "tests" / "fixtures" / "experience_retrieval_eval.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    original_settings_path = db.defaults.settings_db_path
    original_learning_path = db.defaults.learning_db_path
    try:
        with tempfile.TemporaryDirectory(prefix="experience-retrieval-eval-") as temp_dir:
            db.defaults.settings_db_path = str(Path(temp_dir) / "settings.db")
            db.defaults.learning_db_path = str(Path(temp_dir) / "learning.db")
            db.init_databases()
            id_to_slug = _seed_experiences(dataset)
            result = evaluate_ranked_queries(
                dataset["queries"],
                lambda query, variants, limit: search_relevant_experiences(
                    query, query_variants=variants, limit=limit
                ),
                identifier=lambda item: id_to_slug.get(int(item["id"]), str(item["id"])),
                k=5,
            )
            with learning_connection() as connection:
                cache_rows = int(connection.execute(
                    "SELECT COUNT(*) FROM learning_experience_embeddings"
                ).fetchone()[0])
            result.update({
                "dataset": str(dataset_path.relative_to(ROOT)),
                "dataset_type": "synthetic_bilingual",
                "embedding_model": str(get_settings().get("ollama_embedding_model", "nomic-embed-text")),
                "retrieval_mode": "hybrid_vector_and_lexical" if cache_rows else "lexical_fallback_no_vectors_cached",
                "embedding_cache_rows": cache_rows,
                "note": (
                    "Metrics are for this synthetic fixture, not a claim of general retrieval quality. "
                    "When the configured local embedding model is unavailable, the search falls back to lexical ranking."
                ),
            })
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.defaults.settings_db_path = original_settings_path
        db.defaults.learning_db_path = original_learning_path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
