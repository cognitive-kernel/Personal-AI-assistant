"""Small, dependency-free metrics for repeatable retrieval evaluation."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


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


def missing_indexed_relevant_sources(
    connection: Any, cases: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Return relevance URLs that do not have indexed source chunks."""
    relevant_urls: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValueError(f"Case {index} must be an object.")
        relevant = case.get("relevant_ids")
        if (
            not isinstance(relevant, list)
            or not relevant
            or any(not isinstance(value, str) or not value.strip() for value in relevant)
        ):
            raise ValueError(f"Case {index} must contain non-empty string IDs in relevant_ids.")
        relevant_urls.update(relevant)

    missing: list[str] = []
    for url in sorted(relevant_urls):
        row = connection.execute(
            """SELECT 1 FROM knowledge_sources s
               WHERE s.url=? AND s.status='indexed'
               AND EXISTS (SELECT 1 FROM knowledge_chunks c WHERE c.source_id=s.id)
               LIMIT 1""",
            (url,),
        ).fetchone()
        if row is None:
            missing.append(url)
    return missing


def recall_at_k(relevant_ids: Iterable[str], ranked_ids: Sequence[str], k: int = 5) -> float:
    if k < 1:
        raise ValueError("k must be at least 1.")
    relevant = {str(value) for value in relevant_ids}
    if not relevant:
        return 0.0
    return len(relevant.intersection(str(value) for value in ranked_ids[:k])) / len(relevant)


def reciprocal_rank(relevant_ids: Iterable[str], ranked_ids: Sequence[str]) -> float:
    relevant = {str(value) for value in relevant_ids}
    for rank, value in enumerate(ranked_ids, start=1):
        if str(value) in relevant:
            return 1.0 / rank
    return 0.0


def evaluate_cases(
    cases: Sequence[Mapping[str, Any]],
    searcher: Callable[[str, int], Sequence[Mapping[str, Any]]],
    *,
    k: int = 5,
    id_field: str = "url",
) -> dict[str, Any]:
    """Evaluate queries with explicit relevant IDs; empty/invalid cases are rejected."""
    if k < 1:
        raise ValueError("k must be at least 1.")
    if not cases:
        raise ValueError("Evaluation dataset must contain at least one case.")
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        query = case.get("query")
        relevant = case.get("relevant_ids")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Case {index} must contain a non-empty query.")
        if (
            not isinstance(relevant, list)
            or not relevant
            or any(not isinstance(value, str) or not value.strip() for value in relevant)
        ):
            raise ValueError(f"Case {index} must contain non-empty string IDs in relevant_ids.")
        retrieved = searcher(query, k)
        ranked_ids = [str(item[id_field]) for item in retrieved if item.get(id_field) is not None]
        results.append({
            "query": query,
            "recall_at_k": recall_at_k(relevant, ranked_ids, k),
            "reciprocal_rank": reciprocal_rank(relevant, ranked_ids),
            "retrieved_ids": ranked_ids,
            "relevant_ids": [str(value) for value in relevant],
        })
    count = len(results)
    return {
        "queries_evaluated": count,
        "k": k,
        "mean_recall_at_k": sum(item["recall_at_k"] for item in results) / count if count else 0.0,
        "mrr": sum(item["reciprocal_rank"] for item in results) / count if count else 0.0,
        "results": results,
    }
