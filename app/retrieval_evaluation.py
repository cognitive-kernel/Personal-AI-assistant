"""Deterministic retrieval metrics for curated experience-search datasets."""
from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import Any


def _validate_k(k: int) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer.")
    return k


def recall_at_k(ranked_ids: Sequence[Hashable], relevant_ids: Iterable[Hashable], k: int = 5) -> float:
    """Return the fraction of known relevant items found in the first k results."""
    k = _validate_k(k)
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked_ids: Sequence[Hashable], relevant_ids: Iterable[Hashable]) -> float:
    """Return the reciprocal rank of the first relevant result, or zero if absent."""
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    for rank, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant:
            return 1.0 / rank
    return 0.0


def evaluate_ranked_queries(
    cases: Sequence[dict[str, Any]],
    search_fn: Callable[[str, list[str], int], Sequence[Any]],
    *,
    identifier: Callable[[Any], Hashable] | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """Evaluate a search function against cases with query, variants, and relevant IDs."""
    k = _validate_k(k)
    if not cases:
        raise ValueError("At least one evaluation case is required.")
    get_id = identifier or (
        lambda item: item.get("id") if isinstance(item, dict) else item
    )
    per_query = []
    for index, case in enumerate(cases):
        query = case.get("query")
        relevant = case.get("relevant_ids", case.get("relevant_slugs", []))
        variants = case.get("query_variants", [])
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Evaluation case {index} has no query.")
        if not isinstance(relevant, list) or not relevant:
            raise ValueError(f"Evaluation case {index} has no relevant IDs.")
        if not isinstance(variants, list) or any(not isinstance(item, str) for item in variants):
            raise ValueError(f"Evaluation case {index} has invalid query variants.")
        results = list(search_fn(query, variants, max(k, 10)))
        ranked_ids = [get_id(item) for item in results]
        recall = recall_at_k(ranked_ids, relevant, k)
        reciprocal = reciprocal_rank(ranked_ids, relevant)
        per_query.append({
            "id": str(case.get("id", index)),
            "language": str(case.get("language", "und")),
            "recall_at_k": recall,
            "reciprocal_rank": reciprocal,
            "relevant_count": len(set(relevant)),
            "top_results": ranked_ids[:k],
        })
    count = len(per_query)
    return {
        "query_count": count,
        f"recall_at_{k}": sum(item["recall_at_k"] for item in per_query) / count,
        "mrr": sum(item["reciprocal_rank"] for item in per_query) / count,
        "per_query": per_query,
    }
