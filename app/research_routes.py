"""Loopback-only API for queuing persistent research chat tasks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import research_tasks, task_manager
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/research",
    tags=["research-tasks"],
    dependencies=[Depends(require_loopback_client)],
)


class ResearchTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)

    class Config:
        extra = "forbid"


@router.post("/tasks", status_code=202)
def queue_research_task(request: ResearchTaskRequest) -> dict[str, Any]:
    try:
        message, history = research_tasks._validate_payload(
            {"message": request.message, "history": request.history}
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        task = task_manager.create_task(
            research_tasks.RESEARCH_TASK_TYPE,
            {"message": message, "history": history},
            priority=0,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Research task could not be queued.") from exc
    return {"task": task}
