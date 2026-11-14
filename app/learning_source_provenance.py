"""Source-origin contracts and append-only candidate provenance storage."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from .learning_policy_errors import LearningPolicyError

SOURCE_TYPES = frozenset({"web", "ai", "book", "other"})

def _required_text(record: dict[str, Any], field: str, *, maximum: int = 2000) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LearningPolicyError(f"{field} must contain 1–{maximum} characters.")
    return value.strip()


def _https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise LearningPolicyError(f"{field} must be an absolute HTTPS URL.")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() for char in value)
    ):
        raise LearningPolicyError(f"{field} must be a credential-free absolute HTTPS URL.")
    return value.strip()


def validate_learning_source(record: Any) -> dict[str, Any]:
    """Validate one source while preserving optional, source-specific metadata."""
    if not isinstance(record, dict):
        raise LearningPolicyError("A learning source must be an object.")
    source_type = record.get("source_type")
    if not isinstance(source_type, str) or source_type not in SOURCE_TYPES:
        raise LearningPolicyError("source_type must be web, ai, book, or other.")

    normalized = dict(record)
    normalized["source_type"] = source_type
    if source_type == "web":
        normalized["source_url"] = _https_url(record.get("source_url"), "source_url")
        if record.get("title") is not None:
            normalized["title"] = _required_text(record, "title", maximum=500)
    elif source_type == "ai":
        normalized["provider"] = _required_text(record, "provider", maximum=200)
        normalized["model_id"] = _required_text(record, "model_id", maximum=200)
        normalized["reference_url"] = _https_url(record.get("reference_url"), "reference_url")
        if record.get("prompt_version") is not None:
            normalized["prompt_version"] = _required_text(record, "prompt_version", maximum=120)
    elif source_type == "book":
        normalized["book_title"] = _required_text(record, "book_title", maximum=500)
        normalized["book_author"] = _required_text(record, "book_author", maximum=500)
        for field in ("edition", "publisher", "isbn", "page", "chapter"):
            if record.get(field) is not None:
                normalized[field] = _required_text(record, field, maximum=500)
    else:
        normalized["description"] = _required_text(record, "description")
        if record.get("source_url") is not None:
            normalized["source_url"] = _https_url(record["source_url"], "source_url")
    return normalized


def validate_learning_sources(sources: Any) -> list[dict[str, Any]]:
    """Validate a non-empty source bundle; optional extra source records are allowed."""
    if not isinstance(sources, list) or not sources:
        raise LearningPolicyError("At least one explicit learning source is required.")
    return [validate_learning_source(source) for source in sources]


def record_candidate_source(
    candidate_id: int,
    source: Any,
    *,
    verification_status: str = "unverified",
) -> dict[str, Any]:
    """Persist one validated source record without changing candidate trust/status."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise LearningPolicyError("candidate_id must be a positive integer.")
    if not isinstance(verification_status, str) or verification_status not in {"unverified", "verified", "inconclusive", "contradicted", "stale"}:
        raise LearningPolicyError("Unsupported source verification status.")
    normalized = validate_learning_source(source)
    from .db import learning_connection

    fields = (
        "source_type", "source_url", "reference_url", "title", "book_title",
        "book_author", "provider", "model_id", "edition", "publisher", "isbn",
        "page", "chapter", "description",
    )
    values = {field: normalized.get(field) for field in fields}
    metadata = {
        key: value for key, value in normalized.items()
        if key not in {*fields, "source_type"}
    }
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidate = connection.execute(
            "SELECT status, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise LearningPolicyError("Learning candidate does not exist.")
        if candidate["promoted_knowledge_id"] is not None or candidate["status"] == "failed":
            raise LearningPolicyError("Source provenance is immutable after promotion or rejection.")
        existing = connection.execute(
            """SELECT * FROM learning_candidate_sources
               WHERE candidate_id=? AND source_type=?
                 AND COALESCE(source_url,'')=COALESCE(?,'')
                 AND COALESCE(reference_url,'')=COALESCE(?,'')
                 AND COALESCE(book_title,'')=COALESCE(?,'')
                 AND COALESCE(book_author,'')=COALESCE(?,'')
                 AND COALESCE(provider,'')=COALESCE(?,'')
                 AND COALESCE(model_id,'')=COALESCE(?,'')
               ORDER BY id LIMIT 1""",
            (
                candidate_id, values["source_type"], values["source_url"],
                values["reference_url"], values["book_title"], values["book_author"],
                values["provider"], values["model_id"],
            ),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        cursor = connection.execute(
            """INSERT INTO learning_candidate_sources(
                   candidate_id, source_type, source_url, reference_url, title,
                   book_title, book_author, provider, model_id, edition, publisher,
                   isbn, page, chapter, description, metadata_json, verification_status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id, *(values[field] for field in fields),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                verification_status,
            ),
        )
        row = connection.execute(
            "SELECT * FROM learning_candidate_sources WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def list_candidate_sources(candidate_id: int) -> list[dict[str, Any]]:
    """Return source provenance in stable insertion order without changing trust."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise LearningPolicyError("candidate_id must be a positive integer.")
    from .db import learning_connection

    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM learning_candidate_sources
               WHERE candidate_id=? ORDER BY id""",
            (candidate_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def candidate_source_contract_status(connection: Any, candidate_id: int) -> dict[str, Any]:
    """Check mandatory source metadata without confusing provenance with verification."""
    rows = connection.execute(
        """SELECT source_type, source_url, reference_url, book_title, book_author,
                  provider, model_id, description
           FROM learning_candidate_sources WHERE candidate_id=? ORDER BY id""",
        (candidate_id,),
    ).fetchall()
    sources = [dict(row) for row in rows]
    has_ai_reference = any(
        item["source_type"] == "ai"
        and bool(item["provider"])
        and bool(item["model_id"])
        and bool(item["reference_url"])
        for item in sources
    )
    has_origin_record = any(
        (
            item["source_type"] == "web" and bool(item["source_url"])
        ) or (
            item["source_type"] == "book"
            and bool(item["book_title"]) and bool(item["book_author"])
        ) or (
            item["source_type"] == "other" and bool(item["description"])
        )
        for item in sources
    )
    return {
        "source_record_count": len(sources),
        "ai_reference_present": has_ai_reference,
        "origin_source_present": has_origin_record,
        "complete": has_ai_reference and has_origin_record,
    }


