"""Persist external evidence and independent AI consultations for temporary knowledge.

Peer opinions are evidence to inspect, never votes that automatically prove a claim.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import init_databases, learning_connection

_ALLOWED_EVIDENCE_KINDS = {"supporting", "contradicting", "context", "application"}
_ALLOWED_STANCES = {"supports", "contradicts", "mixed", "insufficient_evidence"}


def research_candidate_online(candidate_id: int, query: str, *, limit: int = 3) -> dict[str, Any]:
    """Search and store bounded public-source evidence for a temporary candidate.

    Failure to reach the internet is recorded in the returned diagnostics and never
    deletes or rejects the candidate. Retrieved pages start as context, not proof.
    """
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("query must contain 1–1000 characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit must be between 1 and 5.")
    init_databases()
    with learning_connection() as conn:
        candidate = conn.execute(
            "SELECT id FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
    if candidate is None:
        raise KeyError(candidate_id)

    try:
        from .web_sources import discover_sources
        documents = discover_sources(query.strip(), limit=limit)
    except Exception as exc:
        return {
            "candidate_id": candidate_id, "query": query.strip(), "stored": 0,
            "status": "unavailable", "error": type(exc).__name__,
        }

    stored = 0
    with learning_connection() as conn:
        for document in documents:
            url = str(getattr(document, "url", "")).strip()
            title = str(getattr(document, "title", "")).strip()[:500]
            body = str(getattr(document, "text", "")).strip()
            if not url.startswith(("https://", "http://")) or not body:
                continue
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_review_evidence(
                           candidate_id, evidence_kind, source_url, source_title,
                           source_sha256, excerpt, reliability_note
                       ) VALUES (?, 'context', ?, ?, ?, ?, ?)""",
                    (candidate_id, url, title, digest, body[:4000],
                     "Retrieved automatically from a public web source; not yet classified as supporting or contradicting."),
                )
                stored += int(conn.execute("SELECT changes()").fetchone()[0] > 0)
            except Exception:
                # One malformed/duplicate source must not discard other evidence.
                continue
    return {
        "candidate_id": candidate_id, "query": query.strip(), "stored": stored,
        "found": len(documents), "status": "completed",
    }


def record_peer_consultation(
    candidate_id: int,
    *,
    provider: str,
    prompt: str,
    response: str,
    model_name: str = "",
    claims: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    stance: str = "mixed",
    independence_note: str = "",
) -> dict[str, Any]:
    """Persist an external AI's answer and provenance without treating it as truth."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    for name, value, maximum in (
        ("provider", provider, 120), ("prompt", prompt, 12000),
        ("response", response, 30000), ("model_name", model_name, 200),
        ("independence_note", independence_note, 2000),
    ):
        if not isinstance(value, str) or not value.strip() and name in {"provider", "prompt", "response"}:
            raise ValueError(f"{name} must not be empty.")
        if len(value) > maximum:
            raise ValueError(f"{name} exceeds its size limit.")
    if stance not in _ALLOWED_STANCES:
        raise ValueError("Unsupported consultation stance.")
    claims = [] if claims is None else claims
    citations = [] if citations is None else citations
    if not isinstance(claims, list) or not isinstance(citations, list):
        raise ValueError("claims and citations must be lists.")
    if len(claims) > 100 or len(citations) > 100:
        raise ValueError("Too many claims or citations.")
    try:
        claims_json = json.dumps(claims, ensure_ascii=False, allow_nan=False)
        citations_json = json.dumps(citations, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("claims/citations must be finite JSON data.") from exc
    if len(claims_json) > 20000 or len(citations_json) > 20000:
        raise ValueError("claims/citations exceed the storage limit.")
    digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
    init_databases()
    with learning_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone() is None:
            raise KeyError(candidate_id)
        conn.execute(
            """INSERT OR IGNORE INTO knowledge_peer_consultations(
                   candidate_id, provider, model_name, prompt, response, claims_json,
                   citations_json, stance, independence_note, response_sha256
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, provider.strip(), model_name.strip(), prompt.strip(),
             response.strip(), claims_json, citations_json, stance,
             independence_note.strip(), digest),
        )
        row = conn.execute(
            """SELECT * FROM knowledge_peer_consultations
               WHERE candidate_id=? AND provider=? AND model_name=? AND response_sha256=?""",
            (candidate_id, provider.strip(), model_name.strip(), digest),
        ).fetchone()
    return dict(row)


def get_candidate_review_summary(candidate_id: int) -> dict[str, Any]:
    """Return a transparent evidence inventory; deliberately does not decide truth."""
    init_databases()
    with learning_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone() is None:
            raise KeyError(candidate_id)
        evidence = conn.execute(
            """SELECT evidence_kind, COUNT(*) AS count, COUNT(DISTINCT source_url) AS sources
               FROM knowledge_review_evidence WHERE candidate_id=? GROUP BY evidence_kind""",
            (candidate_id,),
        ).fetchall()
        peers = conn.execute(
            """SELECT stance, COUNT(*) AS count, COUNT(DISTINCT provider) AS providers
               FROM knowledge_peer_consultations WHERE candidate_id=? GROUP BY stance""",
            (candidate_id,),
        ).fetchall()
        peer_count = conn.execute(
            "SELECT COUNT(DISTINCT provider) FROM knowledge_peer_consultations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0]
    return {
        "candidate_id": candidate_id,
        "evidence": [dict(row) for row in evidence],
        "peer_consultations": [dict(row) for row in peers],
        "distinct_peer_providers": int(peer_count),
        "verification_status": "unverified",
        "interpretation": (
            "Web sources and peer-AI responses are review inputs, not votes. "
            "Correlated providers, unsupported citations, and conflicting evidence require explicit testing."
        ),
    }


def record_review_evidence(
    candidate_id: int,
    *,
    evidence_kind: str,
    source_url: str,
    excerpt: str,
    source_title: str = "",
    reliability_note: str = "",
) -> dict[str, Any]:
    """Store a source excerpt with an explicit, revisable evidence classification."""
    if evidence_kind not in _ALLOWED_EVIDENCE_KINDS:
        raise ValueError("Unsupported evidence kind.")
    if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")) or len(source_url) > 2048:
        raise ValueError("source_url must be an absolute HTTP(S) URL.")
    if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 4000:
        raise ValueError("excerpt must contain 1–4000 characters.")
    if not isinstance(source_title, str) or len(source_title) > 500:
        raise ValueError("source_title exceeds its size limit.")
    if not isinstance(reliability_note, str) or len(reliability_note) > 2000:
        raise ValueError("reliability_note exceeds its size limit.")
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    init_databases()
    with learning_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone() is None:
            raise KeyError(candidate_id)
        conn.execute(
            """INSERT OR IGNORE INTO knowledge_review_evidence(
                   candidate_id, evidence_kind, source_url, source_title,
                   source_sha256, excerpt, reliability_note
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, evidence_kind, source_url, source_title, digest,
             excerpt.strip(), reliability_note.strip()),
        )
        row = conn.execute(
            """SELECT * FROM knowledge_review_evidence
               WHERE candidate_id=? AND source_url=? AND source_sha256=?""",
            (candidate_id, source_url, digest),
        ).fetchone()
    return dict(row)
