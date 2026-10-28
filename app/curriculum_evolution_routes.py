"""Loopback-only API for generating and reviewing curriculum evolution proposals."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import curriculum_evolution
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning",
    tags=["curriculum-evolution"],
    dependencies=[Depends(require_loopback_client)],
)


class CurriculumProposalReviewRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=20)
    note: str = Field(min_length=20, max_length=1000)

    model_config = ConfigDict(extra="forbid")


@router.post("/paths/{path_id}/curriculum-proposals/generate")
def generate_curriculum_proposals(path_id: int) -> dict[str, Any]:
    try:
        return curriculum_evolution.generate_proposals(path_id=path_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Curriculum proposals could not be generated.") from exc


@router.get("/paths/{path_id}/curriculum-proposals")
def get_curriculum_proposals(path_id: int, limit: int = 50) -> dict[str, Any]:
    try:
        return curriculum_evolution.list_proposals(path_id=path_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Curriculum proposals could not be loaded.") from exc


@router.post("/curriculum-proposals/{proposal_id}/review")
def review_curriculum_proposal(
    proposal_id: int, request: CurriculumProposalReviewRequest
) -> dict[str, Any]:
    try:
        return curriculum_evolution.review_proposal(
            proposal_id,
            decision=request.decision,
            note=request.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Curriculum proposal not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Curriculum proposal could not be reviewed.") from exc
