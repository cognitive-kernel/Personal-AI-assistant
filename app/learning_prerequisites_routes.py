"""Loopback API for editing explicit learning prerequisite graphs."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import learning_prerequisites
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/paths",
    tags=["learning-prerequisites"],
    dependencies=[Depends(require_loopback_client)],
)


class PrerequisiteModeRequest(BaseModel):
    mode: Literal["linear", "custom"]
    model_config = ConfigDict(extra="forbid")


class PrerequisiteListRequest(BaseModel):
    prerequisite_topic_ids: list[StrictInt] = Field(max_length=100)
    model_config = ConfigDict(extra="forbid")


@router.patch("/{path_id}/prerequisite-mode")
def update_prerequisite_mode(path_id: int, request: PrerequisiteModeRequest) -> dict[str, Any]:
    try:
        return learning_prerequisites.set_path_mode(path_id, request.mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{path_id}/topics/{topic_id}/prerequisites")
def read_topic_prerequisites(path_id: int, topic_id: int) -> dict[str, Any]:
    try:
        return learning_prerequisites.get_topic_prerequisites(path_id, topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc


@router.put("/{path_id}/topics/{topic_id}/prerequisites")
def replace_topic_prerequisites(
    path_id: int, topic_id: int, request: PrerequisiteListRequest
) -> dict[str, Any]:
    try:
        return learning_prerequisites.set_topic_prerequisites(
            path_id, topic_id, request.prerequisite_topic_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
