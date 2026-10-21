"""Loopback-only sandbox submission and explicit consent API."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt

from . import sandbox_consent, sandbox_tasks, task_manager
from .db import get_settings
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/sandbox",
    tags=["sandbox"],
    dependencies=[Depends(require_loopback_client)],
)


class ConsentGrantRequest(BaseModel):
    confirm: StrictBool


class SandboxRunRequest(BaseModel):
    source_code: str = Field(min_length=1, max_length=65536)
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=120)
    max_output_bytes: StrictInt = Field(default=32768, ge=1, le=65536)
    priority: StrictInt = Field(default=0, ge=-100000, le=100000)


def _configured() -> bool:
    try:
        return sandbox_tasks.handler_from_environment() is not None
    except ValueError:
        return False


@router.get("/consent")
def get_sandbox_consent() -> dict[str, Any]:
    try:
        consent = sandbox_consent.get_consent()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent storage is unavailable.") from exc
    return {
        "consent": consent,
        "runtime_configured": _configured(),
        "execution_enabled": bool(consent["enabled"] and _configured()),
    }


@router.post("/consent")
def grant_sandbox_consent(request: ConsentGrantRequest) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required.")
    if not _configured():
        raise HTTPException(
            status_code=409,
            detail="Sandbox is not configured. Verify SANDBOX_ENABLED, Docker executable, and image digest first.",
        )
    try:
        return {"consent": sandbox_consent.grant_consent()}
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent could not be stored.") from exc


@router.delete("/consent")
def revoke_sandbox_consent() -> dict[str, Any]:
    try:
        return {"consent": sandbox_consent.revoke_consent()}
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent could not be revoked.") from exc


@router.post("/run", status_code=202)
def queue_sandbox_execution(request: SandboxRunRequest) -> dict[str, Any]:
    configured_source_limit = int(get_settings().get("programming_source_max_chars", 12000))
    if len(request.source_code) > configured_source_limit:
        raise HTTPException(
            status_code=422,
            detail=f"source_code exceeds the configured programming source limit of {configured_source_limit} characters.",
        )
    if "\x00" in request.source_code:
        raise HTTPException(status_code=422, detail="source_code must not contain NUL characters.")
    if len(request.source_code.encode("utf-8")) > 64 * 1024:
        raise HTTPException(status_code=422, detail="source_code exceeds the 64 KiB limit.")
    try:
        consent = sandbox_consent.get_consent()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent storage is unavailable.") from exc
    if not consent["enabled"]:
        raise HTTPException(status_code=409, detail="Sandbox execution requires explicit user consent.")
    if not _configured():
        raise HTTPException(status_code=409, detail="Sandbox execution is not configured.")
    payload = {
        "source_code": request.source_code,
        "timeout_seconds": request.timeout_seconds,
        "max_output_bytes": request.max_output_bytes,
    }
    try:
        task = task_manager.create_task(
            sandbox_tasks.SANDBOX_TASK_TYPE,
            payload,
            priority=request.priority,
            max_attempts=1,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox task could not be queued.") from exc
    return {
        "task": task,
        "message": "Sandbox execution queued. Results are available through the local task API.",
    }
