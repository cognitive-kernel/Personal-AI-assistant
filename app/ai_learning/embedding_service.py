"""Embedding, title deduplication and semantic content comparison for AI Learning."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from typing import Any

from ..db import get_settings
from ..ollama import embed as ollama_embed
from .config import configured_float_setting as _configured_float_setting
from .config import configured_int_setting as _configured_int_setting
from .embedding_identity import embedding_cache_identity

def _dedupe_normalize(value: str) -> str:
    """Normalize titles across case, whitespace, punctuation, and Persian/Arabic letter variants."""
    value = str(value).casefold().replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    value = value.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).strip()


def _title_embeddings(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    model = str(settings.get("ollama_embedding_model") or os.getenv("OLLAMA_EMBEDDING_MODEL") or "nomic-embed-text").strip()
    if not model:
        raise RuntimeError("No embedding model is configured for title duplicate checks.")
    # Keep large outlines from becoming one oversized local-model request.
    vectors: list[list[float]] = []
    batch_size = _configured_int_setting("ai_learning_embedding_batch_size", 64, 1, 256)
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset:offset + batch_size]
        batch_vectors = ollama_embed(batch, model=model)
        if len(batch_vectors) != len(batch) or any(not vector for vector in batch_vectors):
            raise RuntimeError("Embedding model returned incomplete title vectors.")
        vectors.extend([[float(value) for value in vector] for vector in batch_vectors])
    if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
        raise RuntimeError("Embedding model returned title vectors with inconsistent dimensions.")
    return vectors


# Conservative semantic-content similarity threshold; persisted settings may override it.
SEMANTIC_DUPLICATE_THRESHOLD = 0.90

TITLE_SEMANTIC_DUPLICATE_THRESHOLD = 0.985


def _title_tokens(value: str) -> set[str]:
    """Extract meaningful normalized title tokens for a conservative semantic duplicate check."""
    return {token for token in _dedupe_normalize(value).split()
            if len(token) > 1 and token not in {"sql", "server", "2025", "آشنایی", "مقدمه", "معرفی", "بررسی", "کار", "با", "در", "و", "از", "برای"}}


def _filter_duplicate_titles(candidates: list[str], existing_titles: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Skip normalized and near-identical titles, comparing embeddings only for lexical candidates."""
    accepted: list[str] = []
    skipped: list[dict[str, Any]] = []
    canonical_existing: list[str] = []
    known_by_key: dict[str, str] = {}
    for title in existing_titles:
        key = _dedupe_normalize(title)
        if key and key not in known_by_key:
            known_by_key[key] = title
            canonical_existing.append(title)

    pending: list[str] = []
    for title in candidates:
        key = _dedupe_normalize(title)
        duplicate_of = known_by_key.get(key) if key else None
        if duplicate_of is not None or not key:
            skipped.append({
                "title": title, "duplicate_of": duplicate_of or title,
                "score": 1.0, "method": "normalized",
            })
            continue
        known_by_key[key] = title
        pending.append(title)

    if not pending:
        return accepted, skipped

    embedding_cache: dict[str, list[float]] = {}

    def vector_for(title: str, related_titles: list[str]) -> list[float]:
        key = _dedupe_normalize(title)
        missing: list[str] = []
        missing_keys: set[str] = set()
        for candidate in [title, *related_titles]:
            candidate_key = _dedupe_normalize(candidate)
            if candidate_key not in embedding_cache and candidate_key not in missing_keys:
                missing.append(candidate)
                missing_keys.add(candidate_key)
        if missing:
            try:
                vectors = _title_embeddings(missing)
            except Exception as exc:
                raise ValueError(
                    "بررسی معنایی عنوان‌های تکراری در دسترس نیست؛ برای جلوگیری از ثبت موارد تکراری، عملیات انجام نشد. "
                    f"بررسی کنید Ollama و مدل embedding فعال باشند ({type(exc).__name__})."
                ) from exc
            embedding_cache.update({
                _dedupe_normalize(candidate): vector
                for candidate, vector in zip(missing, vectors)
            })
        return embedding_cache[key]

    accepted_vectors: list[tuple[str, set[str]]] = [
        (title, _title_tokens(title)) for title in canonical_existing
    ]
    for title in pending:
        candidate_tokens = _title_tokens(title)
        relevant: list[str] = []
        for existing, existing_tokens in accepted_vectors:
            shared = len(candidate_tokens & existing_tokens)
            union = candidate_tokens | existing_tokens
            overlap = shared / len(union) if union else 0.0
            if shared >= 3 and overlap >= 0.65:
                relevant.append(existing)

        best: tuple[str, float] | None = None
        if relevant:
            candidate_vector = vector_for(title, relevant)
            for existing in relevant:
                score = _embedding_cosine(
                    candidate_vector,
                    embedding_cache[_dedupe_normalize(existing)],
                )
                if score >= _configured_float_setting("ai_learning_title_duplicate_threshold", TITLE_SEMANTIC_DUPLICATE_THRESHOLD, 0.80, 1.0):
                    if best is None or score > best[1]:
                        best = (existing, score)
        if best:
            skipped.append({
                "title": title, "duplicate_of": best[0],
                "score": round(best[1], 4), "method": "embedding+token_overlap",
            })
        else:
            accepted.append(title)
            accepted_vectors.append((title, candidate_tokens))
    return accepted, skipped

def _dedupe_content_hash(value: str) -> str:
    return hashlib.sha256(_dedupe_normalize(value).encode("utf-8")).hexdigest()


def _embedding_cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise RuntimeError("Embedding vectors have inconsistent dimensions.")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _find_semantic_duplicate_content(
    conn: sqlite3.Connection,
    markdown: str,
    *,
    exclude_lesson_id: int | None = None,
    exclude_topic_id: int | None = None,
    ensure_candidate_embedding: bool = False,
) -> dict[str, Any] | None:
    """Find near-identical active lesson content using cached local embeddings.

    Previous versions of the same lesson/topic are intentionally excluded so a
    genuine lesson update can create a new version. If embeddings are unavailable,
    the caller must not save unchecked content.
    """
    normalized = _dedupe_normalize(markdown)
    if not normalized:
        return None
    rows = conn.execute(
        """SELECT cv.id AS version_id, cv.lesson_id, cv.markdown, cv.content_sha256,
                  l.title AS lesson_title, l.topic_id, t.title AS topic_title
           FROM content_versions cv
           JOIN lessons l ON l.id=cv.lesson_id
           JOIN topics t ON t.id=l.topic_id
           WHERE l.deleted_at IS NULL AND t.deleted_at IS NULL
             AND cv.id=(SELECT MAX(cv2.id) FROM content_versions cv2 WHERE cv2.lesson_id=l.id)
             AND cv.review_status NOT IN ('superseded','withdrawn','rejected')
             AND length(trim(COALESCE(cv.markdown,'')))>0
             AND (? IS NULL OR l.id<>?)
             AND (? IS NULL OR l.topic_id<>?)
           ORDER BY cv.id DESC""",
        (exclude_lesson_id, exclude_lesson_id, exclude_topic_id, exclude_topic_id),
    ).fetchall()
    for row in rows:
        if _dedupe_normalize(str(row["markdown"])) == normalized:
            exact_match = {
                "version_id": int(row["version_id"]),
                "lesson_id": int(row["lesson_id"]),
                "topic_id": int(row["topic_id"]),
                "lesson_title": str(row["lesson_title"]),
                "topic_title": str(row["topic_title"]),
                "score": 1.0,
                "method": "exact",
            }
            if not ensure_candidate_embedding:
                return exact_match
            break
    if not rows and not ensure_candidate_embedding:
        return None

    try:
        settings = get_settings()
    except Exception:
        settings = {}
    model = str(
        settings.get("ollama_embedding_model")
        or os.getenv("OLLAMA_EMBEDDING_MODEL")
        or "nomic-embed-text"
    ).strip()
    if not model:
        raise RuntimeError("No embedding model is configured.")
    embedding_cache_model = embedding_cache_identity(settings, model)

    content_by_hash: dict[str, str] = {}
    for row in rows:
        text = str(row["markdown"]).strip()
        if text:
            content_by_hash.setdefault(_dedupe_content_hash(text), text[:_configured_int_setting("ai_learning_embedding_input_max_chars", 6000, 256, 100000)])
    candidate_hash = _dedupe_content_hash(markdown)
    content_by_hash.setdefault(candidate_hash, markdown.strip()[:_configured_int_setting("ai_learning_embedding_input_max_chars", 6000, 256, 100000)])

    cached = conn.execute(
        "SELECT content_sha256,embedding_json FROM semantic_embedding_cache WHERE embedding_model=?",
        (embedding_cache_model,),
    ).fetchall()
    vectors: dict[str, list[float]] = {}
    for row in cached:
        try:
            vector = json.loads(row["embedding_json"])
            if isinstance(vector, list) and vector and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(float(value)) for value in vector
            ):
                vectors[str(row["content_sha256"])] = [float(value) for value in vector]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    missing = [(digest, text) for digest, text in content_by_hash.items() if digest not in vectors]
    embedding_batch_size = _configured_int_setting("ai_learning_embedding_batch_size", 64, 1, 256)
    for offset in range(0, len(missing), embedding_batch_size):
        batch = missing[offset:offset + embedding_batch_size]
        batch_vectors = ollama_embed([text for _, text in batch], model=model)
        if len(batch_vectors) != len(batch):
            raise RuntimeError("Embedding model returned an incomplete batch.")
        for (digest, _), vector in zip(batch, batch_vectors):
            encoded = json.dumps(vector, separators=(",", ":"), allow_nan=False)
            conn.execute(
                """INSERT INTO semantic_embedding_cache(content_sha256,embedding_model,embedding_json)
                   VALUES(?,?,?) ON CONFLICT(content_sha256,embedding_model)
                   DO UPDATE SET embedding_json=excluded.embedding_json""",
                (digest, embedding_cache_model, encoded),
            )
            vectors[digest] = [float(value) for value in vector]

    candidate_vector = vectors.get(candidate_hash)
    if candidate_vector is None:
        raise RuntimeError("Candidate embedding is missing after indexing.")
    best: dict[str, Any] | None = None
    for row in rows:
        text = str(row["markdown"]).strip()
        if not text:
            continue
        vector = vectors.get(_dedupe_content_hash(text))
        if vector is None:
            raise RuntimeError("Existing lesson embedding is missing after indexing.")
        score = _embedding_cosine(candidate_vector, vector)
        if best is None or score > float(best["score"]):
            best = {
                "version_id": int(row["version_id"]),
                "lesson_id": int(row["lesson_id"]),
                "topic_id": int(row["topic_id"]),
                "lesson_title": str(row["lesson_title"]),
                "topic_title": str(row["topic_title"]),
                "score": score,
                "method": "embedding",
            }
    content_duplicate_threshold = _configured_float_setting("ai_learning_content_duplicate_threshold", SEMANTIC_DUPLICATE_THRESHOLD, 0.80, 1.0)
    return best if best is not None and float(best["score"]) >= content_duplicate_threshold else None


