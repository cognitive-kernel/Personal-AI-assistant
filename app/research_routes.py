"""Loopback-only API for queuing persistent research chat tasks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt, ConfigDict

from . import chat_history, db, research_planner, research_tasks, task_manager
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/research",
    tags=["research-tasks"],
    dependencies=[Depends(require_loopback_client)],
)


class ResearchTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    conversation_id: StrictInt | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid")


class ResearchPlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    max_steps: StrictInt = Field(default=4, ge=2, le=5)

    model_config = ConfigDict(extra="forbid")


@router.post("/tasks", status_code=202)
def queue_research_task(request: ResearchTaskRequest) -> dict[str, Any]:
    chat_limit = int(db.get_settings().get("chat_source_max_chars", 12000))
    if len(request.message) > chat_limit:
        raise HTTPException(status_code=422, detail=f"message exceeds the configured chat input limit of {chat_limit} characters.")
    try:
        history = request.history
        if request.conversation_id is not None:
            if chat_history.get_conversation(request.conversation_id) is None:
                raise HTTPException(status_code=404, detail="Chat conversation not found.")
            # Persisted server history is authoritative; browser state is not trusted
            # to reconstruct or overwrite previous turns.
            history = chat_history.get_recent_history(request.conversation_id, limit=30)
        message, normalized_history = research_tasks.validate_payload(
            {"message": request.message, "history": history}
        )
        payload: dict[str, Any] = {"message": message, "history": normalized_history}
        if request.conversation_id is not None:
            payload["conversation_id"] = request.conversation_id
            chat_history.append_user_message(request.conversation_id, message)
        task = task_manager.create_task(
            research_tasks.RESEARCH_TASK_TYPE,
            payload,
            priority=0,
            max_attempts=1,
        )
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat conversation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Research task could not be queued.") from exc
    return {"task": task, "conversation_id": request.conversation_id}



@router.post("/plans", status_code=202)
def queue_research_plan(request: ResearchPlanRequest) -> dict[str, Any]:
    chat_limit = int(db.get_settings().get("chat_source_max_chars", 12000))
    if len(request.goal) > chat_limit:
        raise HTTPException(status_code=422, detail=f"goal exceeds the configured chat input limit of {chat_limit} characters.")
    try:
        goal, max_steps = research_planner.validate_plan_payload(
            {"goal": request.goal, "max_steps": request.max_steps}
        )
        task = task_manager.create_task(
            research_planner.RESEARCH_PLAN_TASK_TYPE,
            {"goal": goal, "max_steps": max_steps},
            priority=0,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Research plan could not be queued.") from exc
    return {
        "task_id": task["id"],
        "task_type": task["task_type"],
        "status": task["status"],
        "max_steps": max_steps,
    }


@router.get("/plans/{task_id}")
def get_research_plan_progress(task_id: int) -> dict[str, Any]:
    if isinstance(task_id, bool) or task_id < 1:
        raise HTTPException(status_code=422, detail="task_id must be a positive integer.")
    task = task_manager.get_task(task_id)
    if task is None or task.get("task_type") != research_planner.RESEARCH_PLAN_TASK_TYPE:
        raise HTTPException(status_code=404, detail="Research plan task not found.")
    checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    progress_source = result or checkpoint
    plan = progress_source.get("plan", [])
    completed_results = progress_source.get("results", progress_source.get("completed_results", []))
    if not isinstance(plan, list):
        plan = []
    if not isinstance(completed_results, list):
        completed_results = []
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "phase": checkpoint.get("phase"),
        "completed_steps": result.get("completed_steps", checkpoint.get("next_step_index", len(completed_results))),
        "total_steps": result.get("total_steps", checkpoint.get("total_steps", len(plan))),
        "plan": plan,
        "results": completed_results,
        "error": task.get("error"),
    }
