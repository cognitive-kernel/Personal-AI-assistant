"""Loopback-only API for queuing persistent research chat tasks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import chat_history, db, research_tasks, task_manager
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/research",
    tags=["research-tasks"],
    dependencies=[Depends(require_loopback_client)],
)


class ResearchTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    conversation_id: int | None = Field(default=None, ge=1)

    class Config:
        extra = "forbid"


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
