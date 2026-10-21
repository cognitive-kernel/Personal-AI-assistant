"""Loopback-only queue API for local programming assistance."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from . import programming_tasks, task_manager
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/programming",
    tags=["programming-assistance"],
    dependencies=[Depends(require_loopback_client)],
)


class ProgrammingTaskRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=6000)
    context: str = Field(default="", max_length=65536)

    model_config = ConfigDict(extra="forbid")


@router.post("/tasks", status_code=202)
def queue_programming_task(request: ProgrammingTaskRequest) -> dict[str, Any]:
    try:
        instruction, context = programming_tasks.validate_payload({
            "instruction": request.instruction,
            "context": request.context,
        })
        task = task_manager.create_task(
            programming_tasks.PROGRAMMING_TASK_TYPE,
            {"instruction": instruction, "context": context},
            priority=0,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Programming task could not be queued.") from exc
    return {
        "task": task,
        "message": "Programming assistance queued. Generated code is not executed automatically.",
    }
