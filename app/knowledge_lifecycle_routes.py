"""Loopback-only endpoints for evidence-bound temporary knowledge review."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import knowledge_lifecycle, task_manager, db
from .knowledge_lifecycle_tasks import KNOWLEDGE_VALIDATE_TASK_TYPE
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/candidates",
    tags=["knowledge-lifecycle"],
    dependencies=[Depends(require_loopback_client)],
)


class CandidateReviewRequest(BaseModel):
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_kind: Literal["source"]
    verdict: Literal["supports", "contradicts", "inconclusive"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=2000)
    model_id: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1500)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class CandidatePromotionRequest(BaseModel):
    expected_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


@router.post("/{candidate_id}/evidence")
def record_candidate_evidence(candidate_id: int, request: CandidateReviewRequest) -> dict[str, Any]:
    try:
        item = knowledge_lifecycle.record_candidate_review(
            candidate_id,
            candidate_sha256=request.candidate_sha256,
            review_kind=request.review_kind,
            verdict=request.verdict,
            reviewer_id=request.reviewer_id,
            note=request.note,
            model_id=request.model_id,
            source_url=request.source_url,
            source_sha256=request.source_sha256,
            input_sha256=request.input_sha256,
            output_sha256=request.output_sha256,
        )
        return {"evidence": item, "readiness": knowledge_lifecycle.candidate_readiness(candidate_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Temporary knowledge candidate not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{candidate_id}/readiness")
def get_candidate_readiness(candidate_id: int) -> dict[str, Any]:
    try:
        return knowledge_lifecycle.candidate_readiness(candidate_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Temporary knowledge candidate not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



@router.post("/{candidate_id}/validate", status_code=202)
def enqueue_candidate_validation(candidate_id: int) -> dict[str, Any]:
    """Schedule source/model validation; network and model work stays in the queue worker."""
    try:
        readiness = knowledge_lifecycle.candidate_readiness(candidate_id)
        if readiness["ready_for_promotion"]:
            return {"candidate_id": candidate_id, "status": readiness["status"], "task": None, "readiness": readiness}
        with db.learning_connection() as connection:
            row = connection.execute(
                "SELECT content_sha256, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Temporary knowledge candidate not found.")
        if row["promoted_knowledge_id"] is not None:
            raise HTTPException(status_code=409, detail="Candidate has already been promoted.")
        task = task_manager.create_task(
            KNOWLEDGE_VALIDATE_TASK_TYPE,
            {"candidate_id": candidate_id, "candidate_sha256": row["content_sha256"]},
            max_attempts=1,
            dedupe_key=f"knowledge-validate:{candidate_id}:{row['content_sha256']}",
        )
        return {"candidate_id": candidate_id, "status": "queued", "task": task, "readiness": readiness}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Temporary knowledge candidate not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Knowledge validation could not be queued.") from exc

@router.post("/{candidate_id}/promote")
def promote_candidate(candidate_id: int, request: CandidatePromotionRequest) -> dict[str, Any]:
    try:
        return knowledge_lifecycle.promote_candidate(
            candidate_id, expected_candidate_sha256=request.expected_candidate_sha256
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Temporary knowledge candidate not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
