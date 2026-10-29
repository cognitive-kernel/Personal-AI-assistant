"""Explicit, bounded source freshness checks for factual knowledge.

A changed or unavailable source moves an active claim to needs_review. This
module never upgrades evidence levels and never treats a model review as truth.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any
from urllib.parse import urlparse, urlunparse

from . import web_sources
from .db import init_databases, learning_connection

MAX_SOURCES_PER_CLAIM_REFRESH = 3
_REVIEW_REQUIRED_OUTCOMES = {"changed", "excerpt_missing", "unavailable", "blocked"}


def _normalized_https_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Saved source URL is invalid.")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Only saved HTTPS source URLs can be revalidated.")
    if parsed.username or parsed.password:
        raise ValueError("Saved source URLs containing credentials are blocked.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Saved source URL has an invalid port.") from exc
    if port not in {None, 443}:
        raise ValueError("Only standard HTTPS ports can be revalidated.")
    return urlunparse(parsed._replace(fragment=""))


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _fingerprint(title: str, text: str) -> str:
    normalized = title.strip() + "\n" + " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def list_refresh_queue(limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200.")
    init_databases()
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT k.id, k.claim, k.domain_name, k.environment_name,
                      k.environment_version, k.evidence_level, k.lifecycle_status,
                      k.risk_level, k.updated_at
               FROM factual_knowledge AS k
               WHERE k.lifecycle_status='active'
                 AND EXISTS (
                     SELECT 1 FROM factual_evidence AS e
                     WHERE e.knowledge_id=k.id
                       AND e.source_type IN ('official_docs','source_code')
                       AND e.source_ref LIKE 'https://%'
                 )
               ORDER BY k.updated_at, k.id
               LIMIT ?""",
            (limit,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            evidence = connection.execute(
                """SELECT source_type, source_ref, source_hash, excerpt
                   FROM factual_evidence
                   WHERE knowledge_id=?
                     AND source_type IN ('official_docs','source_code')
                     AND source_ref LIKE 'https://%'
                   ORDER BY id""",
                (item["id"],),
            ).fetchall()
            item["evidence"] = [dict(source) for source in evidence]
            items.append(item)
    return items


def refresh_claim_sources(knowledge_id: int, *, confirm: bool) -> dict[str, Any]:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise ValueError("knowledge_id must be a positive integer.")
    if confirm is not True:
        raise ValueError("Explicit confirmation to fetch saved source URLs is required.")
    init_databases()
    with learning_connection() as connection:
        claim = connection.execute(
            "SELECT id, lifecycle_status, evidence_level FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        if claim is None:
            raise KeyError(knowledge_id)
        rows = connection.execute(
            """SELECT source_ref, source_hash, excerpt
               FROM factual_evidence
               WHERE knowledge_id=?
                 AND source_type IN ('official_docs','source_code')
                 AND source_ref LIKE 'https://%'
               ORDER BY id""",
            (knowledge_id,),
        ).fetchall()

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        raw_url = str(row["source_ref"])
        if raw_url in seen:
            continue
        seen.add(raw_url)
        sources.append({
            "source_ref": raw_url,
            "evidence_source_hash": str(row["source_hash"] or ""),
            "excerpt": str(row["excerpt"] or ""),
        })
        if len(sources) >= MAX_SOURCES_PER_CLAIM_REFRESH:
            break
    if not sources:
        raise ValueError("Factual claim has no eligible saved HTTPS official/source-code evidence.")

    results: list[dict[str, Any]] = []
    for source in sources:
        source_ref = source["source_ref"]
        excerpt_hash = hashlib.sha256(source["excerpt"].encode("utf-8")).hexdigest() if source["excerpt"] else None
        with learning_connection() as connection:
            previous = connection.execute(
                """SELECT current_content_sha256 FROM factual_source_revalidation_history
                   WHERE knowledge_id=? AND source_ref=? AND current_content_sha256 IS NOT NULL
                   ORDER BY checked_at DESC, id DESC LIMIT 1""",
                (knowledge_id, source_ref),
            ).fetchone()
        previous_hash = str(previous["current_content_sha256"]) if previous else None
        current_hash = None
        title = ""
        error_kind = ""
        try:
            normalized_url = _normalized_https_url(source_ref)
            web_sources._validate_public_url(normalized_url)
        except (ValueError, web_sources.SourceFetchError):
            outcome = "blocked"
            error_kind = "SourcePolicyRejected"
        else:
            try:
                document = web_sources._fetch_document(normalized_url, "", max_chars=6000)
                title = document.title[:300]
                current_hash = _fingerprint(document.title, document.text)
                normalized_excerpt = _normalized_text(source["excerpt"])
                normalized_document = _normalized_text(document.text)
                if not normalized_excerpt or normalized_excerpt not in normalized_document:
                    outcome = "excerpt_missing"
                elif previous_hash is None:
                    outcome = "baseline"
                elif previous_hash == current_hash:
                    outcome = "unchanged"
                else:
                    outcome = "changed"
            except web_sources.SourceFetchError:
                outcome = "unavailable"
                error_kind = "SourceFetchError"

        init_databases()
        with learning_connection() as connection:
            connection.execute(
                """INSERT INTO factual_source_revalidation_history(
                       knowledge_id, source_ref, outcome, previous_content_sha256,
                       current_content_sha256, evidence_excerpt_sha256, error_kind
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (knowledge_id, source_ref, outcome, previous_hash, current_hash, excerpt_hash, error_kind),
            )
        results.append({
            "source_ref": source_ref,
            "outcome": outcome,
            "previous_content_sha256": previous_hash,
            "current_content_sha256": current_hash,
            "evidence_excerpt_sha256": excerpt_hash,
            "error_kind": error_kind,
        })

    needs_review = any(item["outcome"] in _REVIEW_REQUIRED_OUTCOMES for item in results)
    with learning_connection() as connection:
        if needs_review:
            connection.execute(
                """UPDATE factual_knowledge
                   SET lifecycle_status='needs_review', updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND lifecycle_status='active'""",
                (knowledge_id,),
            )
        current = connection.execute(
            "SELECT lifecycle_status, evidence_level FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
    return {
        "knowledge_id": knowledge_id,
        "checks": results,
        "counts": dict(Counter(item["outcome"] for item in results)),
        "needs_review": needs_review,
        "lifecycle_status": current["lifecycle_status"],
        "evidence_level": current["evidence_level"],
        "verified": False,
        "note": "Source checks are freshness signals only. They never prove truth or promote an evidence level.",
    }


def list_source_checks(knowledge_id: int, limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise ValueError("knowledge_id must be a positive integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200.")
    init_databases()
    with learning_connection() as connection:
        if connection.execute("SELECT 1 FROM factual_knowledge WHERE id=?", (knowledge_id,)).fetchone() is None:
            raise KeyError(knowledge_id)
        rows = connection.execute(
            """SELECT id, knowledge_id, source_ref, outcome, previous_content_sha256,
                      current_content_sha256, evidence_excerpt_sha256, error_kind, checked_at
               FROM factual_source_revalidation_history
               WHERE knowledge_id=? ORDER BY checked_at DESC, id DESC LIMIT ?""",
            (knowledge_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
