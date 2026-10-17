"""Hybrid lexical/vector retrieval for verified learning experiences.

Embeddings are a rebuildable local cache keyed by experience version and model.
If local Ollama embeddings are unavailable, retrieval fails soft to lexical ranking.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Sequence

from . import experience_feedback, ollama
from .db import get_settings, learning_connection
from .experience import list_experiences

_RRF_K = 60
_MIN_COSINE = 0.20
_MAX_CANDIDATES = 200
_MAX_VECTOR_RESULTS = 30
_TOKEN_RE = re.compile(r"[\w-]{2,}", flags=re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _search_text(item: dict[str, Any]) -> str:
    evidence = item.get("source_evidence", [])
    claims = " ".join(
        str(entry.get("claim", "")) for entry in evidence if isinstance(entry, dict)
    )
    return " ".join(
        str(item.get(key, "")) for key in ("topic", "problem", "hypothesis", "takeaway")
    ) + " " + claims


def _eligible(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        results = item.get("test_results", [])
        legacy = (
            any(isinstance(r, dict) and r.get("runner") == "chat-learning-loop-v1" for r in results)
            and not any(
                isinstance(r, dict) and r.get("test") == "semantic_claim_review" and r.get("passed") is True
                for r in results
            )
        )
        if not legacy:
            result.append(item)
    return result


def _item_sort_key(items: list[dict[str, Any]], item_id: int) -> tuple[str, int]:
    item = next(item for item in items if int(item["id"]) == item_id)
    return str(item.get("updated_at", "")), int(item["id"])


def _lexical_ranking(items: list[dict[str, Any]], variants: Sequence[str]) -> list[tuple[int, float]]:
    documents = {int(item["id"]): _tokens(_search_text(item)) for item in items}
    query_terms = {token for variant in variants for token in _tokens(variant)}
    if not query_terms:
        return []
    df: Counter[str] = Counter()
    for tokens in documents.values():
        df.update(set(tokens))
    count = max(1, len(documents))
    average_length = sum(map(len, documents.values())) / count
    ranked = []
    for item in items:
        item_id = int(item["id"])
        tokens = documents[item_id]
        if not tokens:
            continue
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            idf = math.log1p((count - df[term] + 0.5) / (df[term] + 0.5))
            denominator = frequency + 1.2 * (0.25 + 0.75 * len(tokens) / max(1.0, average_length))
            score += idf * frequency * 2.2 / denominator
        if score > 0:
            ranked.append((item_id, score))
    ranked.sort(key=lambda pair: (pair[1], _item_sort_key(items, pair[0])), reverse=True)
    return ranked


def _cosine(left: list[float], right: list[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    if norm_left == 0 or norm_right == 0:
        return None
    return dot / (norm_left * norm_right)


def _cached_embeddings(item_versions: list[tuple[int, int]], model: str) -> dict[tuple[int, int], list[float]]:
    if not item_versions:
        return {}
    cached = {}
    with learning_connection() as connection:
        for offset in range(0, len(item_versions), 400):
            chunk = item_versions[offset:offset + 400]
            placeholders = ",".join("(?, ?)" for _ in chunk)
            params: list[Any] = [model]
            for item_id, version in chunk:
                params.extend((item_id, version))
            rows = connection.execute(
                f"""SELECT experience_id, experience_version, embedding_json
                    FROM learning_experience_embeddings
                    WHERE model=? AND (experience_id, experience_version) IN ({placeholders})""",
                params,
            ).fetchall()
            for row in rows:
                try:
                    vector = json.loads(row["embedding_json"])
                except (TypeError, ValueError):
                    continue
                if isinstance(vector, list) and vector and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                    for value in vector
                ):
                    cached[(int(row["experience_id"]), int(row["experience_version"]))] = [
                        float(value) for value in vector
                    ]
    return cached


def _save_embeddings(items: list[dict[str, Any]], vectors: list[list[float]], model: str) -> None:
    if len(items) != len(vectors):
        raise ValueError("Embedding cache item/vector count mismatch.")
    with learning_connection() as connection:
        for item, vector in zip(items, vectors):
            connection.execute(
                """INSERT OR REPLACE INTO learning_experience_embeddings(
                       experience_id, experience_version, model, dimensions, embedding_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (item["id"], item["version"], model, len(vector),
                 json.dumps(vector, separators=(",", ":"), allow_nan=False)),
            )


def _vector_ranking(items: list[dict[str, Any]], variants: Sequence[str]) -> list[tuple[int, float]]:
    if not items:
        return []
    model = str(get_settings().get("ollama_embedding_model", "nomic-embed-text")).strip()
    if not model:
        return []
    query_vectors = ollama.embed(list(variants[:8]), model=model)
    if not query_vectors:
        return []

    cached = _cached_embeddings([(int(item["id"]), int(item["version"])) for item in items], model)
    missing = [
        item for item in items
        if (int(item["id"]), int(item["version"])) not in cached
    ]
    for offset in range(0, len(missing), 32):
        batch = missing[offset:offset + 32]
        vectors = ollama.embed([_search_text(item)[:6000] for item in batch], model=model)
        _save_embeddings(batch, vectors, model)
        for item, vector in zip(batch, vectors):
            cached[(int(item["id"]), int(item["version"]))] = vector

    ranked = []
    for item in items:
        vector = cached.get((int(item["id"]), int(item["version"])))
        if vector is None:
            continue
        similarities = [
            score for query_vector in query_vectors
            if (score := _cosine(query_vector, vector)) is not None
        ]
        if similarities and max(similarities) >= _MIN_COSINE:
            ranked.append((int(item["id"]), max(similarities)))
    ranked.sort(key=lambda pair: (pair[1], _item_sort_key(items, pair[0])), reverse=True)
    return ranked[:_MAX_VECTOR_RESULTS]


def _source_freshness(item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Estimate evidence observation freshness without claiming that a URL was re-fetched."""
    evidence = item.get("source_evidence", [])
    if not isinstance(evidence, list) or not evidence:
        return {"status": "unknown", "multiplier": 0.80}
    now = now or datetime.now(timezone.utc)
    multipliers: list[float] = []
    ages: list[float] = []
    unknown_count = 0
    for entry in evidence:
        observed_at = entry.get("observed_at") if isinstance(entry, dict) else None
        if not isinstance(observed_at, str) or not observed_at.strip():
            multipliers.append(0.80)
            unknown_count += 1
            continue
        try:
            parsed = datetime.fromisoformat(observed_at.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 86400)
        except (TypeError, ValueError, OverflowError):
            multipliers.append(0.80)
            unknown_count += 1
            continue
        ages.append(age_days)
        if age_days <= 30:
            multipliers.append(1.0)
        elif age_days <= 90:
            multipliers.append(0.75)
        else:
            multipliers.append(0.45)
    multiplier = min(multipliers, default=0.80)
    if unknown_count:
        status = "stale_or_unknown"
    elif multiplier >= 1.0:
        status = "recently_observed"
    elif multiplier >= 0.75:
        status = "aging"
    elif multiplier >= 0.5:
        status = "stale"
    else:
        status = "stale_or_unknown"
    return {
        "status": status,
        "multiplier": multiplier,
        "oldest_observation_days": round(max(ages), 1) if ages else None,
    }


def search_relevant_experiences(
    query: str, query_variants: list[str] | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """Rank verified experiences by lexical/vector relevance adjusted for evidence age."""
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        result_limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError, OverflowError):
        result_limit = 5
    variants = [query.strip()]
    for variant in query_variants or []:
        if isinstance(variant, str) and variant.strip() and variant.strip() not in variants:
            variants.append(variant.strip())
        if len(variants) >= 8:
            break
    items = _eligible(list_experiences(status="verified", limit=_MAX_CANDIDATES))
    if not items:
        return []
    lexical = _lexical_ranking(items, variants)
    lexical_ranks = {item_id: rank for rank, (item_id, _) in enumerate(lexical, 1)}
    try:
        vector = _vector_ranking(items, variants)
    except ollama.OllamaError:
        vector = []
    vector_ranks = {item_id: rank for rank, (item_id, _) in enumerate(vector, 1)}
    try:
        feedback_map = experience_feedback.get_feedback_map([int(item["id"]) for item in items])
    except Exception:
        # Ranking remains available if the optional feedback store is unavailable.
        feedback_map = {}
    ranked: list[tuple[float, tuple[str, int], int, dict[str, Any]]] = []
    for item in items:
        item_id = int(item["id"])
        relevance = 0.0
        if item_id in lexical_ranks:
            relevance += 1.0 / (_RRF_K + lexical_ranks[item_id])
        if item_id in vector_ranks:
            relevance += 1.0 / (_RRF_K + vector_ranks[item_id])
        if relevance <= 0:
            continue
        freshness = _source_freshness(item)
        feedback = feedback_map.get(item_id)
        feedback_multiplier = 1.10 if feedback and feedback["rating"] > 0 else (
            0.75 if feedback and feedback["rating"] < 0 else 1.0
        )
        score = relevance * float(freshness["multiplier"]) * feedback_multiplier
        enriched = dict(item)
        enriched["source_freshness"] = freshness
        enriched["retrieval_feedback"] = feedback
        ranked.append((score, _item_sort_key(items, item_id), item_id, enriched))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[3] for row in ranked[:result_limit]]
