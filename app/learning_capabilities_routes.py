"""Loopback-only API for the evidence-bounded learner capability map."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import learning_capabilities
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning",
    tags=["learning-capabilities"],
    dependencies=[Depends(require_loopback_client)],
)


@router.get("/capabilities")
def get_capabilities(path_id: int | None = None, limit: int = 200) -> dict[str, Any]:
    try:
        return learning_capabilities.get_capability_map(path_id=path_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Capability map could not be loaded.") from exc
