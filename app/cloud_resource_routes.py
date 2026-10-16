"""Loopback-only API for viewing and triggering cloud resource discovery."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from . import cloud_resources
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/cloud-resources",
    tags=["cloud-resources"],
    dependencies=[Depends(require_loopback_client)],
)


@router.get("")
def list_cloud_resources(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        return {
            "latest_run": cloud_resources.latest_discovery_run(),
            "candidates": cloud_resources.list_candidates(limit=limit),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource discovery storage is unavailable.") from exc


@router.post("/discover", status_code=status.HTTP_202_ACCEPTED)
def request_cloud_discovery() -> dict[str, Any]:
    try:
        task = cloud_resources.enqueue_discovery_task(manual=True)
        return {"task": task, "message": "Cloud resource discovery queued; candidates remain unreviewed until verified."}
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource discovery could not be queued.") from exc
