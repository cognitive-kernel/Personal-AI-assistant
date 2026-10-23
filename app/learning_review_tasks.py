"""Durable task handler for independent web and peer-AI review of lesson candidates."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from . import learning_review_channels as channels
from .task_manager import create_task
from .task_worker import PermanentTaskError, RetryableTaskError, TaskContext

LEARNING_REVIEW_TASK_TYPE = "learning.review_candidate"
_ALLOWED_KEYS = {"candidate_id", "query", "web_mode"}


def enqueue_candidate_review(
    candidate_id: int, *, content_sha256: str, query: str, web_mode: str = "search",
    request_id: str,
) -> dict[str, Any]:
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(content_sha256, str) or len(content_sha256) != 64:
        raise ValueError("content_sha256 must be a SHA-256 hex digest.")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("query must contain 1–1000 characters.")
    if web_mode not in {"search", "disabled"}:
        raise ValueError("web_mode must be search or disabled.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise ValueError("request_id is required and must be at most 100 characters.")
    payload = {
        "candidate_id": candidate_id,
        "query": query.strip(),
        "web_mode": web_mode,
    }
    dedupe_key = f"learning-candidate-review:{candidate_id}:{content_sha256}:{request_id.strip()}"
    return create_task(
        LEARNING_REVIEW_TASK_TYPE,
        payload,
        priority=0,
        max_attempts=2,
        dedupe_key=dedupe_key,
    )


def handle_candidate_review(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_KEYS:
        raise PermanentTaskError("Candidate review task payload contains unsupported fields.")
    candidate_id = payload.get("candidate_id")
    query = payload.get("query")
    web_mode = payload.get("web_mode", "search")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise PermanentTaskError("candidate_id must be a positive integer.")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise PermanentTaskError("query must contain 1–1000 characters.")
    if web_mode not in {"search", "disabled"}:
        raise PermanentTaskError("web_mode must be search or disabled.")

    try:
        with channels.learning_connection() as connection:
            row = connection.execute(
                "SELECT content, content_sha256, topic FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise PermanentTaskError("Learning candidate not found.")
        content_hash = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
        if content_hash != str(row["content_sha256"]):
            raise PermanentTaskError("Candidate content hash mismatch; review is blocked.")
    except PermanentTaskError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RetryableTaskError("Candidate storage is temporarily unavailable.") from exc

    context.checkpoint({
        "phase": "candidate_review_started",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "web_mode": web_mode,
    })
    results: dict[str, Any] = {}
    if web_mode == "disabled":
        try:
            results["web_research"] = channels.record_review_attempt(
                candidate_id,
                channel="web_research",
                status="unconfigured",
                details={"reason": "disabled_by_local_user"},
            )
        except Exception as exc:
            raise RetryableTaskError("Could not record disabled web-review state.") from exc
    else:
        try:
            results["web_research"] = channels.research_candidate_online(
                candidate_id, query.strip(), limit=3
            )
        except Exception as exc:
            try:
                channels.record_review_attempt(
                    candidate_id, channel="web_research", status="failed",
                    details={"error_type": type(exc).__name__},
                )
            except Exception as db_exc:
                raise RetryableTaskError("Could not persist web-review failure.") from db_exc
            results["web_research"] = {"status": "failed", "error_type": type(exc).__name__}

    context.checkpoint({
        "phase": "candidate_review_peer_ai",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
    })
    try:
        results["peer_ai"] = channels.consult_configured_peer_ais(candidate_id, limit=3)
    except Exception as exc:
        try:
            results["peer_ai"] = channels.record_review_attempt(
                candidate_id, channel="peer_ai", status="failed",
                details={"error_type": type(exc).__name__},
            )
            results["peer_ai"]["status"] = "failed"
        except Exception as db_exc:
            raise RetryableTaskError("Could not persist peer-review failure.") from db_exc

    context.checkpoint({
        "phase": "candidate_review_completed",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "web_status": str(results.get("web_research", {}).get("status", "unknown")),
        "peer_status": str(results.get("peer_ai", {}).get("status", "unknown")),
    })
    return {
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "channels": results,
        "promoted": False,
        "note": "Web pages and peer responses are review material only; no trust or candidate status was promoted.",
    }
