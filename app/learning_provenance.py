"""Strict source provenance and aggregate autonomy policy for learning.

This module contains pure validation/evaluation rules. It never promotes knowledge,
mutates candidate status, or treats model confidence as evidence of correctness.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

LEARNING_PATHS = frozenset({
    "ai_learning",
    "basic_learning",
    "curriculum_learning",
    "experience_system",
})
SOURCE_TYPES = frozenset({"web", "ai", "book", "other"})
VERIFICATION_METHODS = frozenset({
    "official_source_comparison",
    "deterministic_oracle",
    "sandbox_test",
    "independent_model",
    "human_adjudication",
})


class LearningProvenanceError(ValueError):
    """A learning source record is incomplete or violates its contract."""


def _required_text(record: dict[str, Any], field: str, *, maximum: int = 2000) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LearningProvenanceError(f"{field} must contain 1–{maximum} characters.")
    return value.strip()


def _https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise LearningProvenanceError(f"{field} must be an absolute HTTPS URL.")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() for char in value)
    ):
        raise LearningProvenanceError(f"{field} must be a credential-free absolute HTTPS URL.")
    return value.strip()


def validate_learning_source(record: Any) -> dict[str, Any]:
    """Validate one source while preserving optional, source-specific metadata."""
    if not isinstance(record, dict):
        raise LearningProvenanceError("A learning source must be an object.")
    source_type = record.get("source_type")
    if not isinstance(source_type, str) or source_type not in SOURCE_TYPES:
        raise LearningProvenanceError("source_type must be web, ai, book, or other.")

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
        raise LearningProvenanceError("At least one explicit learning source is required.")
    return [validate_learning_source(source) for source in sources]


def evaluate_aggregate_learning_accuracy(records: Any) -> dict[str, Any]:
    """Calculate the cross-path accuracy gate from independently evidenced evaluations.

    The gate is strictly greater than 95%, pools the four learning paths, and requires
    every path to be represented. Empty, duplicated, self-verified, or untraceable
    evaluations cannot enable autonomous acceptance.
    """
    if not isinstance(records, list):
        raise LearningProvenanceError("Evaluation records must be a list.")
    seen: set[str] = set()
    counts = {
        path: {"correct": 0, "incorrect": 0, "total": 0}
        for path in sorted(LEARNING_PATHS)
    }
    total_correct = 0
    total = 0
    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(record, dict):
            raise LearningProvenanceError(f"{prefix} must be an object.")
        sample_id = _required_text(record, "sample_id", maximum=200)
        if sample_id in seen:
            raise LearningProvenanceError(f"{prefix}.sample_id is duplicated.")
        seen.add(sample_id)
        path = record.get("learning_path")
        if not isinstance(path, str) or path not in LEARNING_PATHS:
            raise LearningProvenanceError(f"{prefix}.learning_path is unsupported.")
        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"correct", "incorrect"}:
            raise LearningProvenanceError(f"{prefix}.outcome must be correct or incorrect.")
        method = record.get("verification_method")
        if not isinstance(method, str) or method not in VERIFICATION_METHODS:
            raise LearningProvenanceError(f"{prefix}.verification_method is unsupported.")
        _required_text(record, "verifier_id", maximum=200)
        _required_text(record, "evidence_ref", maximum=2048)
        evaluated_at = _required_text(record, "evaluated_at", maximum=80)
        try:
            timestamp = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LearningProvenanceError(f"{prefix}.evaluated_at must be ISO-8601.") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise LearningProvenanceError(f"{prefix}.evaluated_at must include a timezone.")
        generator_id = record.get("generator_id")
        verifier_id = record["verifier_id"]
        if method == "independent_model":
            generator = _required_text(record, "generator_id", maximum=200)
            if generator.casefold() == verifier_id.strip().casefold():
                raise LearningProvenanceError(f"{prefix} cannot be verified by its generating model.")
        counts[path][outcome] += 1
        counts[path]["total"] += 1
        total += 1
        if outcome == "correct":
            total_correct += 1

    accuracy = (total_correct / total) if total else None
    represented_paths = [path for path, value in counts.items() if value["total"] > 0]
    eligible = bool(total) and len(represented_paths) == len(LEARNING_PATHS) and accuracy > 0.95
    return {
        "policy_version": "aggregate-learning-accuracy-v1",
        "threshold_exclusive": 0.95,
        "sample_count": total,
        "correct_count": total_correct,
        "incorrect_count": total - total_correct,
        "accuracy": accuracy,
        "represented_paths": represented_paths,
        "by_path": counts,
        "autonomous_acceptance_eligible": eligible,
        "reason": (
            "aggregate_accuracy_above_95_percent"
            if eligible else
            "insufficient_evidence_or_accuracy_or_learning_path_coverage"
        ),
        "note": (
            "Eligibility applies only to routine learning-content acceptance. "
            "It does not authorize destructive operations, security-sensitive changes, "
            "production actions, or bypass of safety and access controls."
        ),
    }


def record_candidate_source(
    candidate_id: int,
    source: Any,
    *,
    verification_status: str = "unverified",
) -> dict[str, Any]:
    """Persist one validated source record without changing candidate trust/status."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise LearningProvenanceError("candidate_id must be a positive integer.")
    if verification_status not in {"unverified", "verified", "inconclusive", "contradicted", "stale"}:
        raise LearningProvenanceError("Unsupported source verification status.")
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
        if connection.execute(
            "SELECT 1 FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone() is None:
            raise LearningProvenanceError("Learning candidate does not exist.")
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
                candidate_id, *(values[field] for field in fields[1:]),
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
        raise LearningProvenanceError("candidate_id must be a positive integer.")
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
