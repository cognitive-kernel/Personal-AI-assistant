"""Loopback-only endpoints for evidence-bound temporary knowledge review."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import knowledge_lifecycle
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/candidates",
    tags=["knowledge-lifecycle"],
    dependencies=[Depends(require_loopback_client)],
)


class CandidateReviewRequest(BaseModel):
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_kind: Literal["source", "ai_review", "test_review"]
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
