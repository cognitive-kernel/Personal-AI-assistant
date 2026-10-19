"""HTTP routes for observing and controlling durable background tasks."""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import task_manager
from .task_manager import TaskManagerError, TaskNotFoundError

def require_loopback_client(request: Request) -> None:
    """Keep task-control endpoints local until an authenticated remote API exists."""
    if request.client is None:
        raise HTTPException(status_code=403, detail="Task management is available from localhost only.")
    try:
        is_loopback = ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=403, detail="Task management is available from localhost only.")


router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_loopback_client)],
)


class TaskCreateRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any]
    priority: int = Field(default=0, ge=-100000, le=100000)
    max_attempts: int = Field(default=1, ge=1, le=20)
    scheduled_at: datetime | None = None


class TaskPriorityRequest(BaseModel):
    priority: int = Field(ge=-100000, le=100000)


class TaskScheduleRequest(BaseModel):
    scheduled_at: datetime


def _raise_task_error(exc: TaskManagerError) -> None:
    if isinstance(exc, TaskNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("", status_code=202)
def create_task(request: TaskCreateRequest) -> dict[str, Any]:
    if request.task_type.strip() == "sandbox.run_python":
        raise HTTPException(
            status_code=422,
            detail="Submit sandbox execution through POST /api/sandbox/run so consent and audit checks are enforced.",
        )
    try:
        return task_manager.create_task(
            request.task_type,
            request.payload,
            priority=request.priority,
            max_attempts=request.max_attempts,
            scheduled_at=request.scheduled_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_tasks(
    statuses: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> dict[str, Any]:
    try:
        return task_manager.list_tasks_page(statuses=statuses, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{task_id}/events")
def list_task_events(
    task_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Read durable status-transition events for one task."""
    try:
        return task_manager.list_task_status_events(
            task_id,
            limit=limit,
            before_id=before_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/{task_id}/pause")
def pause_task(task_id: int) -> dict[str, Any]:
    try:
        return task_manager.request_pause(task_id)
    except TaskManagerError as exc:
        _raise_task_error(exc)


@router.post("/{task_id}/resume")
def resume_task(task_id: int) -> dict[str, Any]:
    try:
        return task_manager.resume_task(task_id)
    except TaskManagerError as exc:
        _raise_task_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int) -> dict[str, Any]:
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["task_type"] == "learning.run_job":
        job_id = task["payload"].get("job_id")
        if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0:
            from .learning_tasks import cancel_learning_job

            try:
                cancelled = cancel_learning_job(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if cancelled is not None:
                return cancelled
    try:
        return task_manager.request_cancel(task_id)
    except TaskManagerError as exc:
        _raise_task_error(exc)


@router.patch("/{task_id}/priority")
def set_task_priority(task_id: int, request: TaskPriorityRequest) -> dict[str, Any]:
    try:
        return task_manager.update_task_priority(task_id, request.priority)
    except TaskManagerError as exc:
        _raise_task_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{task_id}/schedule")
def reschedule_task(task_id: int, request: TaskScheduleRequest) -> dict[str, Any]:
    try:
        return task_manager.reschedule_task(task_id, request.scheduled_at)
    except TaskManagerError as exc:
        _raise_task_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
