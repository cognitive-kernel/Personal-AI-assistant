"""Loopback-only API for auditable lesson-source synchronization checks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import learning_source_sync
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/source-sync",
    tags=["learning-source-sync"],
    dependencies=[Depends(require_loopback_client)],
)


class SourceSyncCheckRequest(BaseModel):
    limit: StrictInt = Field(default=5, ge=1, le=5)

    model_config = ConfigDict(extra="forbid")


class SourceSyncReviewRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=20, max_length=1000)

    model_config = ConfigDict(extra="forbid")


@router.post("/check")
def check_lesson_sources(request: SourceSyncCheckRequest) -> dict[str, Any]:
    try:
        return learning_source_sync.check_sources(limit=request.limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Lesson source synchronization could not be completed.") from exc


@router.post("/events/{event_id}/regenerate")
def regenerate_lesson_from_reviewed_source(event_id: int) -> dict[str, Any]:
    try:
        return learning_source_sync.queue_lesson_regeneration(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source-sync event not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Lesson regeneration could not be queued.") from exc


@router.post("/events/{event_id}/review")
def review_source_change(event_id: int, request: SourceSyncReviewRequest) -> dict[str, Any]:
    try:
        return learning_source_sync.record_review_decision(
            event_id,
            decision=request.decision,
            note=request.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source-sync event not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Source-change decision could not be saved.") from exc
