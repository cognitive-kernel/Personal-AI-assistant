#!/usr/bin/env python3
"""Run the bilingual experience-retrieval benchmark against an isolated temporary DB."""
from __future__ import annotations

import argparse
import json
import math
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


def apply_acceptance_thresholds(
    metrics: dict[str, Any],
    *,
    min_recall_at_k: float = 0.70,
    min_mrr: float = 0.65,
    max_zero_recall_rate: float = 0.20,
) -> dict[str, Any]:
    """Add deterministic, fail-closed acceptance gates to retrieval metrics."""
    thresholds = {
        "min_recall_at_k": min_recall_at_k,
        "min_mrr": min_mrr,
        "max_zero_recall_rate": max_zero_recall_rate,
    }
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not 0 <= value <= 1
        for value in thresholds.values()
    ):
        raise ValueError("Retrieval acceptance thresholds must be numbers between 0 and 1.")
    recall_key = next((key for key in metrics if key.startswith("recall_at_")), None)
    per_query = metrics.get("per_query")
    if (
        not isinstance(metrics, dict)
        or recall_key is None
        or isinstance(metrics.get(recall_key), bool)
        or not isinstance(metrics.get(recall_key), (int, float))
        or not math.isfinite(metrics[recall_key])
        or not 0 <= metrics[recall_key] <= 1
        or isinstance(metrics.get("mrr"), bool)
        or not isinstance(metrics.get("mrr"), (int, float))
        or not math.isfinite(metrics["mrr"])
        or not 0 <= metrics["mrr"] <= 1
        or not isinstance(per_query, list) or not per_query
        or any(
            not isinstance(item, dict)
            or isinstance(item.get("recall_at_k"), bool)
            or not isinstance(item.get("recall_at_k"), (int, float))
            or not math.isfinite(item["recall_at_k"])
            or not 0 <= item["recall_at_k"] <= 1
            for item in per_query
        )
    ):
        return {
            **metrics,
            "acceptance": {"passed": False, "status": "invalid_metrics", "gates": {}},
        }
    zero_recall_rate = sum(item["recall_at_k"] <= 0 for item in per_query) / len(per_query)
    gates = {
        "minimum_recall_at_k": metrics[recall_key] >= min_recall_at_k,
        "minimum_mean_reciprocal_rank": metrics["mrr"] >= min_mrr,
        "maximum_zero_recall_query_rate": zero_recall_rate <= max_zero_recall_rate,
    }
    return {
        **metrics,
        "zero_recall_query_rate": round(zero_recall_rate, 4),
        "acceptance": {
            "passed": all(gates.values()),
            "status": "passed" if all(gates.values()) else "failed",
            "thresholds": thresholds,
            "gates": gates,
            "interpretation": (
                "Acceptance applies only to this versioned synthetic benchmark and does not prove "
                "general retrieval quality or scientific correctness."
            ),
        },
    }


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
    parser.add_argument("--min-recall-at-k", type=float, default=0.70)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--max-zero-recall-rate", type=float, default=0.20)
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
            result = apply_acceptance_thresholds(
                result,
                min_recall_at_k=args.min_recall_at_k,
                min_mrr=args.min_mrr,
                max_zero_recall_rate=args.max_zero_recall_rate,
            )
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
            exit_code = 0 if result.get("acceptance", {}).get("passed") is True else 1
    finally:
        db.defaults.settings_db_path = original_settings_path
        db.defaults.learning_db_path = original_learning_path
    return locals().get("exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
