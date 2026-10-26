"""Multi-source review gates for temporary learning candidates.

A candidate remains temporary until evidence is bound to its exact content hash.
A single failed test or unavailable source is inconclusive, never a deletion signal.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse
from typing import Any

from .db import get_settings, init_databases, learning_connection

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_KINDS = {"source", "ai_review", "test_review"}
_VERDICTS = {"supports", "contradicts", "inconclusive"}


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _candidate_row(connection, candidate_id: int):
    row = connection.execute(
        """SELECT id, job_id, topic, content, source_kind, source_urls_json,
                  content_sha256, quality_report_json, status, test_plan_json,
                  test_results_json, promoted_knowledge_id
           FROM temporary_knowledge_candidates WHERE id=?""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise KeyError(candidate_id)
    actual = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
    if actual != row["content_sha256"]:
        raise ValueError("Candidate content hash mismatch; review and promotion are blocked.")
    return row


def record_candidate_review(
    candidate_id: int,
    *,
    candidate_sha256: str,
    review_kind: str,
    verdict: str,
    reviewer_id: str,
    note: str,
    model_id: str | None = None,
    source_url: str | None = None,
    source_sha256: str | None = None,
    input_sha256: str | None = None,
    output_sha256: str | None = None,
    _trusted_adapter: bool = False,
) -> dict[str, Any]:
    """Append immutable, content-bound evidence; caller-supplied verdicts are not proof."""
    init_databases()
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    candidate_sha256 = _digest(candidate_sha256, "candidate_sha256")
    if review_kind not in _REVIEW_KINDS or verdict not in _VERDICTS:
        raise ValueError("Unsupported review kind or verdict.")
    if review_kind != "source" and _trusted_adapter is not True:
        raise ValueError("AI/test evidence can only be written by a trusted internal adapter, not the public API.")
    if not isinstance(reviewer_id, str) or not 1 <= len(reviewer_id.strip()) <= 200:
        raise ValueError("reviewer_id must contain 1–200 characters.")
    if not isinstance(note, str) or not 20 <= len(note.strip()) <= 2000:
        raise ValueError("note must contain 20–2000 characters.")
    host = None
    if review_kind == "source":
        if not isinstance(source_url, str) or len(source_url) > 1500:
            raise ValueError("Source review requires an HTTPS URL.")
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ValueError("Source review requires a credential-free HTTPS URL.")
        source_sha256 = _digest(source_sha256, "source_sha256")
        # Bind the claimed source digest to a safe live fetch instead of accepting a caller-supplied hash.
        from . import web_sources
        try:
            live_document = web_sources.fetch_public_document(
                source_url, "temporary learning candidate review", max_chars=100000
            )
        except Exception as exc:
            raise ValueError(
                "Source could not be safely fetched; record the review as inconclusive outside this endpoint."
            ) from exc
        observed_source_hash = hashlib.sha256(live_document.text.encode("utf-8")).hexdigest()
        if observed_source_hash != source_sha256:
            raise ValueError("Source hash does not match the currently fetched public document.")
    elif source_url is not None or source_sha256 is not None:
        raise ValueError("Only source reviews may include a source URL/hash.")
    if review_kind == "ai_review":
        if not isinstance(model_id, str) or not 1 <= len(model_id.strip()) <= 200:
            raise ValueError("AI review requires a stable model/provider identifier.")
        input_sha256 = _digest(input_sha256, "input_sha256")
        output_sha256 = _digest(output_sha256, "output_sha256")
    elif model_id is not None:
        raise ValueError("Only AI reviews may include model_id.")
    if review_kind == "test_review":
        input_sha256 = _digest(input_sha256, "input_sha256")
        output_sha256 = _digest(output_sha256, "output_sha256")
    elif review_kind != "ai_review" and (input_sha256 is not None or output_sha256 is not None):
        raise ValueError("Only AI/test reviews may include input/output hashes.")

    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidate = _candidate_row(connection, candidate_id)
        if candidate_sha256 != candidate["content_sha256"]:
            raise ValueError("Review candidate hash is stale; refresh the candidate before recording evidence.")
        cursor = connection.execute(
            """INSERT INTO knowledge_candidate_reviews(
                   candidate_id, candidate_sha256, review_kind, verdict, reviewer_id,
                   model_id, source_url, source_host, source_sha256,
                   input_sha256, output_sha256, note
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, candidate_sha256, review_kind, verdict, reviewer_id.strip(),
             model_id.strip() if isinstance(model_id, str) else None,
             source_url, host, source_sha256, input_sha256, output_sha256, note.strip()),
        )
        row = connection.execute(
            """SELECT id, candidate_id, candidate_sha256, review_kind, verdict,
                      reviewer_id, model_id, source_url, source_host, source_sha256,
                      input_sha256, output_sha256, note, created_at
               FROM knowledge_candidate_reviews WHERE id=?""",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def candidate_readiness(candidate_id: int) -> dict[str, Any]:
    """Compute conservative readiness from distinct, current-hash evidence."""
    init_databases()
    with learning_connection() as connection:
        candidate = _candidate_row(connection, candidate_id)
        reviews = [dict(row) for row in connection.execute(
            """SELECT * FROM knowledge_candidate_reviews
               WHERE candidate_id=? AND candidate_sha256=?
               ORDER BY id""",
            (candidate_id, candidate["content_sha256"]),
        ).fetchall()]
        all_reviews = int(connection.execute(
            "SELECT COUNT(*) FROM knowledge_candidate_reviews WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0])
    sources = {
        item["source_host"] for item in reviews
        if item["review_kind"] == "source" and item["verdict"] == "supports"
        and item["source_host"]
    }
    ai_models = {
        item["model_id"] for item in reviews
        if item["review_kind"] == "ai_review" and item["verdict"] == "supports"
        and item["model_id"]
    }
    contradictions = [
        {"kind": item["review_kind"], "reviewer_id": item["reviewer_id"], "note": item["note"][:300]}
        for item in reviews if item["verdict"] == "contradicts"
    ]
    try:
        test_results = json.loads(candidate["test_results_json"])
    except (TypeError, json.JSONDecodeError):
        test_results = []
    executed_test_passed = (
        candidate["status"] == "passed"
        and isinstance(test_results, list)
        and bool(test_results)
        and any(isinstance(item, dict) and item.get("passed") is True for item in test_results)
    )
    settings = get_settings()
    generation_models = {
        str(settings.get("learning_ollama_model", "")).strip(),
        str(settings.get("ollama_model", "")).strip(),
    } - {""}
    independent_models = ai_models - generation_models
    checks = {
        "content_hash_valid": True,
        "two_supporting_source_hosts": len(sources) >= 2,
        "two_distinct_independent_ai_reviewers": len(independent_models) >= 2,
        "controlled_test_passed": executed_test_passed,
        "no_unresolved_contradictions": not contradictions,
        "not_already_promoted": candidate["promoted_knowledge_id"] is None,
    }
    ready = all(checks.values())
    return {
        "candidate_id": candidate_id,
        "candidate_sha256": candidate["content_sha256"],
        "status": "ready_for_promotion" if ready else (
            "conflicting" if contradictions else "awaiting_evidence"
        ),
        "ready_for_promotion": ready,
        "checks": checks,
        "evidence_counts": {
            "current_hash_reviews": len(reviews),
            "historical_hash_reviews": all_reviews - len(reviews),
            "supporting_source_hosts": len(sources),
            "supporting_ai_models": len(ai_models),
            "independent_ai_models": len(independent_models),
            "contradictions": len(contradictions),
        },
        "contradictions": contradictions,
        "note": (
            "Readiness is a policy gate, not a scientific guarantee. Review records must come "
            "from trusted adapters; source-host diversity alone does not prove organizational independence."
        ),
    }


def promote_candidate(candidate_id: int, *, expected_candidate_sha256: str) -> dict[str, Any]:
    """Promote an eligible temporary candidate into durable knowledge without deleting history."""
    expected_candidate_sha256 = _digest(expected_candidate_sha256, "expected_candidate_sha256")
    init_databases()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidate = _candidate_row(connection, candidate_id)
        if candidate["content_sha256"] != expected_candidate_sha256:
            raise ValueError("Candidate changed after review; promotion is blocked.")
        if candidate["promoted_knowledge_id"] is not None:
            row = connection.execute(
                "SELECT id, topic, version, status FROM knowledge_items WHERE id=?",
                (candidate["promoted_knowledge_id"],),
            ).fetchone()
            return {"promoted": True, "already_promoted": True, "knowledge": dict(row) if row else None}
        # Recompute all gates under the same write transaction to prevent a TOCTOU race.
        reviews = [dict(row) for row in connection.execute(
            """SELECT * FROM knowledge_candidate_reviews
               WHERE candidate_id=? AND candidate_sha256=? ORDER BY id""",
            (candidate_id, expected_candidate_sha256),
        ).fetchall()]
        sources = {
            item["source_host"] for item in reviews
            if item["review_kind"] == "source" and item["verdict"] == "supports" and item["source_host"]
        }
        ai_models = {
            item["model_id"] for item in reviews
            if item["review_kind"] == "ai_review" and item["verdict"] == "supports" and item["model_id"]
        }
        settings = get_settings()
        generation_models = {
            str(settings.get("learning_ollama_model", "")).strip(),
            str(settings.get("ollama_model", "")).strip(),
        } - {""}
        independent_models = ai_models - generation_models
        contradictions = any(item["verdict"] == "contradicts" for item in reviews)
        try:
            test_results = json.loads(candidate["test_results_json"])
        except (TypeError, json.JSONDecodeError):
            test_results = []
        tested = (
            candidate["status"] == "passed" and isinstance(test_results, list) and bool(test_results)
            and any(isinstance(item, dict) and item.get("passed") is True for item in test_results)
        )
        if not (len(sources) >= 2 and len(independent_models) >= 2 and tested and not contradictions):
            raise ValueError("Candidate has not met every multi-source, independent-review, and executed-test gate.")
        knowledge = connection.execute(
            "SELECT id, topic, version, status FROM knowledge_items WHERE job_id=?",
            (candidate["job_id"],),
        ).fetchone()
        if knowledge is None:
            raise ValueError("Durable knowledge row for this candidate is missing.")
        connection.execute(
            "UPDATE knowledge_items SET status='retained_after_review' WHERE id=? AND status='unverified'",
            (knowledge["id"],),
        )
        cursor = connection.execute(
            """UPDATE temporary_knowledge_candidates
               SET promoted_knowledge_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND promoted_knowledge_id IS NULL AND content_sha256=?""",
            (knowledge["id"], candidate_id, expected_candidate_sha256),
        )
        if cursor.rowcount != 1:
            raise ValueError("Candidate changed during promotion; retry after refreshing state.")
        promoted = connection.execute(
            "SELECT id, topic, version, status FROM knowledge_items WHERE id=?",
            (knowledge["id"],),
        ).fetchone()
    return {
        "promoted": True,
        "already_promoted": False,
        "candidate_id": candidate_id,
        "knowledge": dict(promoted),
        "note": "The temporary candidate and immutable review history remain available for audit.",
    }
