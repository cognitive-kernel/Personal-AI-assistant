"""Loopback-only endpoints for queuing and inspecting candidate evidence review."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .db import learning_connection
from .learning_review_tasks import enqueue_candidate_review
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/candidates",
    tags=["learning-candidate-review"],
    dependencies=[Depends(require_loopback_client)],
)


class CandidateReviewRequest(BaseModel):
    confirm: StrictBool
    request_id: UUID
    query: str | None = Field(default=None, max_length=1000)
    web_mode: Literal["search", "disabled"] = "search"

    model_config = ConfigDict(extra="forbid")


def _decode(value: str, field: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Stored review {field} is invalid.") from exc


@router.post("/{candidate_id}/review", status_code=202)
def queue_candidate_review(candidate_id: int, request: CandidateReviewRequest) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation to start evidence review is required.")
    if isinstance(candidate_id, bool) or candidate_id < 1:
        raise HTTPException(status_code=422, detail="candidate_id must be a positive integer.")
    try:
        with learning_connection() as connection:
            row = connection.execute(
                """SELECT id, topic, content, content_sha256, status
                   FROM temporary_knowledge_candidates WHERE id=?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Learning candidate not found.")
        actual_hash = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
        if actual_hash != str(row["content_sha256"]):
            raise HTTPException(status_code=409, detail="Candidate content hash mismatch; review is blocked.")
        if row["status"] != "awaiting_test":
            raise HTTPException(status_code=409, detail="Only temporary candidates awaiting tests can be reviewed.")
        query = (request.query or str(row["topic"])).strip()
        if not query or len(query) > 1000:
            raise HTTPException(status_code=422, detail="query must contain 1–1000 characters.")
        task = enqueue_candidate_review(
            candidate_id,
            content_sha256=actual_hash,
            query=query,
            web_mode=request.web_mode,
            request_id=str(request.request_id),
        )
        return {
            "candidate_id": candidate_id,
            "task_id": task["id"],
            "task_status": task["status"],
            "deduplicated": task["created_at"] != task.get("updated_at"),
            "note": "Review runs in the durable background queue. Web and peer responses are not proof and do not promote knowledge.",
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Candidate review could not be queued.") from exc


@router.get("/{candidate_id}/review-history")
def get_candidate_review_history(candidate_id: int) -> dict[str, Any]:
    if isinstance(candidate_id, bool) or candidate_id < 1:
        raise HTTPException(status_code=422, detail="candidate_id must be a positive integer.")
    try:
        with learning_connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Learning candidate not found.")
            attempts = connection.execute(
                """SELECT id, channel, status, provider, details_json, created_at
                   FROM knowledge_review_attempts WHERE candidate_id=?
                   ORDER BY id DESC LIMIT 50""",
                (candidate_id,),
            ).fetchall()
            evidence = connection.execute(
                """SELECT id, evidence_kind, source_url, source_title, source_sha256,
                          excerpt, reliability_note, fetched_at
                   FROM knowledge_review_evidence WHERE candidate_id=?
                   ORDER BY id DESC LIMIT 20""",
                (candidate_id,),
            ).fetchall()
            peers = connection.execute(
                """SELECT id, provider, model_name, prompt_version, stance, response,
                          citations_json, response_sha256, independence_note, created_at
                   FROM knowledge_peer_consultations WHERE candidate_id=?
                   ORDER BY id DESC LIMIT 10""",
                (candidate_id,),
            ).fetchall()
        return {
            "candidate_id": candidate_id,
            "attempts": [
                {**dict(row), "details": _decode(row["details_json"], "attempt details")}
                for row in attempts
            ],
            "evidence": [
                {**dict(row), "excerpt": str(row["excerpt"])[:1200]}
                for row in evidence
            ],
            "peer_consultations": [
                {
                    "id": row["id"],
                    "provider": row["provider"],
                    "model_name": row["model_name"],
                    "prompt_version": row["prompt_version"],
                    "stance": row["stance"],
                    "response_excerpt": str(row["response"])[:2500],
                    "citations": _decode(row["citations_json"], "peer citations"),
                    "response_sha256": row["response_sha256"],
                    "independence_note": row["independence_note"],
                    "created_at": row["created_at"],
                }
                for row in peers
            ],
            "promoted": False,
        }
    except HTTPException:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Candidate review history is unavailable.") from exc
