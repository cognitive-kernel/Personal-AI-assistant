"""Loopback-only API for user-curated, versioned learning experiences.

The API deliberately cannot mark an experience verified or submit test results.
Only a controlled runner may attach actual execution evidence and promote records.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from . import experience
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/experiences",
    tags=["experience-memory"],
    dependencies=[Depends(require_loopback_client)],
)

EditableStatus = Literal[
    "identified",
    "researching",
    "ready_to_test",
    "testing",
    "failed",
    "needs_review",
]


class ExperienceCreateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    problem: str = Field(min_length=1, max_length=10000)
    hypothesis: str = Field(default="", max_length=5000)

    class Config:
        extra = "forbid"


class ExperienceUpdateRequest(BaseModel):
    status: EditableStatus | None = None
    source_evidence: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    test_plan: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    takeaway: str | None = Field(default=None, max_length=10000)

    class Config:
        # In particular, callers cannot smuggle test_results or the internal
        # _controlled_run switch through the public HTTP boundary.
        extra = "forbid"


@router.get("")
def list_experiences(
    topic: str | None = Query(default=None, max_length=300),
    experience_status: EditableStatus | Literal["verified"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        items = experience.list_experiences(topic=topic, status=experience_status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "count": len(items)}


@router.get("/search")
def search_experiences(
    query: str = Query(min_length=1, max_length=3000),
    limit: int = Query(default=5, ge=1, le=10),
) -> dict[str, Any]:
    try:
        verified = experience.search_relevant_experiences(query, limit=limit)
        warnings = experience.search_relevant_failures(query, limit=min(3, limit))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience memory search is unavailable.") from exc
    return {"verified": verified, "warnings": warnings}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_experience(request: ExperienceCreateRequest) -> dict[str, Any]:
    try:
        item = experience.create_experience(request.topic, request.problem, request.hypothesis)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"experience": item}


@router.get("/{experience_id}/versions")
def get_experience_versions(experience_id: int) -> dict[str, Any]:
    try:
        versions = experience.list_experience_versions(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    return {"experience_id": experience_id, "versions": versions}


@router.get("/{experience_id}")
def get_experience(experience_id: int) -> dict[str, Any]:
    try:
        item = experience.get_experience(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    return {"experience": item}


@router.patch("/{experience_id}")
def update_experience(
    experience_id: int,
    request: ExperienceUpdateRequest,
) -> dict[str, Any]:
    changes = request.model_dump(exclude_unset=True)
    if not changes or all(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="At least one editable field is required.")
    try:
        item = experience.update_experience(experience_id, **changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "controlled experience runner" in str(exc) else 422, detail=str(exc)) from exc
    return {"experience": item}
