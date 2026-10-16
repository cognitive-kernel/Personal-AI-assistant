"""Loopback-only API for governed self-improvement proposals."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool

from . import self_improvement
from .self_improvement import ProposalError
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/self-improvement",
    tags=["self-improvement"],
    dependencies=[Depends(require_loopback_client)],
)


class ProposalCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=4000)
    patch: str = Field(min_length=1, max_length=262144)

    class Config:
        extra = "forbid"


class ExplicitConfirmation(BaseModel):
    confirm: StrictBool

    class Config:
        extra = "forbid"


class RejectProposalRequest(ExplicitConfirmation):
    reason: str = Field(default="", max_length=1000)


def _proposal_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="Self-improvement proposal not found.") from exc
    if isinstance(exc, ProposalError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Self-improvement proposal request was invalid.") from exc


@router.get("/proposals")
def list_proposals(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    try:
        return {"proposals": self_improvement.list_proposals(limit)}
    except ProposalError as exc:
        _proposal_error(exc)


@router.post("/proposals", status_code=201)
def create_proposal(request: ProposalCreateRequest) -> dict[str, Any]:
    try:
        proposal = self_improvement.create_proposal(request.title, request.rationale, request.patch)
        return {"proposal": proposal}
    except ProposalError as exc:
        _proposal_error(exc)


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int) -> dict[str, Any]:
    try:
        proposal = self_improvement.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Self-improvement proposal not found.")
        return {"proposal": proposal}
    except ProposalError as exc:
        _proposal_error(exc)


@router.get("/proposals/{proposal_id}/events")
def get_proposal_events(proposal_id: int) -> dict[str, Any]:
    try:
        return {"events": self_improvement.proposal_events(proposal_id)}
    except (ProposalError, KeyError) as exc:
        _proposal_error(exc)


@router.post("/proposals/{proposal_id}/validate")
def validate_proposal(proposal_id: int) -> dict[str, Any]:
    try:
        return {"proposal": self_improvement.validate_proposal(proposal_id)}
    except (ProposalError, KeyError) as exc:
        _proposal_error(exc)


@router.post("/proposals/{proposal_id}/static-check")
def static_check_proposal(
    proposal_id: int, request: ExplicitConfirmation
) -> dict[str, Any]:
    try:
        return {
            "proposal": self_improvement.static_check_proposal(
                proposal_id, confirm=request.confirm
            )
        }
    except (ProposalError, KeyError) as exc:
        _proposal_error(exc)


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, request: ExplicitConfirmation) -> dict[str, Any]:
    try:
        return {"proposal": self_improvement.approve_for_pr(proposal_id, confirm=request.confirm)}
    except (ProposalError, KeyError) as exc:
        _proposal_error(exc)


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, request: RejectProposalRequest) -> dict[str, Any]:
    try:
        return {
            "proposal": self_improvement.reject_proposal(
                proposal_id, confirm=request.confirm, reason=request.reason
            )
        }
    except (ProposalError, KeyError) as exc:
        _proposal_error(exc)
