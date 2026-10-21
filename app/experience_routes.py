"""Loopback-only API for user-curated, versioned learning experiences.

The API deliberately cannot mark an experience verified or submit test results.
Only a controlled runner may attach actual execution evidence and promote records.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StrictBool

from . import experience, experience_capture_consent, experience_feedback, experience_source_checks, experience_tasks, sandbox_consent, task_manager
from .task_routes import require_loopback_client
from .pagination import metadata as pagination_metadata

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


class ExperienceAutoCaptureConsentRequest(BaseModel):
    confirm: StrictBool

    class Config:
        extra = "forbid"


class ExperienceSourceRefreshRequest(BaseModel):
    confirm: StrictBool

    class Config:
        extra = "forbid"


class ExperienceFeedbackRequest(BaseModel):
    rating: Literal[-1, 1]

    class Config:
        extra = "forbid"


@router.get("/auto-capture/consent")
def get_auto_capture_consent() -> dict[str, Any]:
    try:
        return {"consent": experience_capture_consent.get_consent()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience capture consent storage is unavailable.") from exc


@router.post("/auto-capture/consent")
def grant_auto_capture_consent(
    request: ExperienceAutoCaptureConsentRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit consent confirmation is required.")
    try:
        return {"consent": experience_capture_consent.grant_consent()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience capture consent could not be stored.") from exc


@router.delete("/auto-capture/consent")
def revoke_auto_capture_consent() -> dict[str, Any]:
    try:
        return {"consent": experience_capture_consent.revoke_consent()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience capture consent could not be revoked.") from exc


@router.get("")
def list_experiences(
    topic: str | None = Query(default=None, max_length=300),
    experience_status: EditableStatus | Literal["verified"] | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    size = limit if limit is not None else page_size
    try:
        items = experience.list_experiences(topic=topic, status=experience_status, limit=size, offset=(page - 1) * size)
        total = experience.count_experiences(topic=topic, status=experience_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "count": total, "pagination": pagination_metadata(page, size, total)}

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


class ExperienceTestRunRequest(BaseModel):
    confirm: StrictBool
    test_name: str = Field(min_length=1, max_length=300)
    source_code: str = Field(min_length=1, max_length=65536)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    max_output_bytes: int = Field(default=32768, ge=1, le=65536)

    class Config:
        extra = "forbid"


@router.post("/{experience_id}/run-test", status_code=status.HTTP_202_ACCEPTED)
def queue_experience_test(
    experience_id: int,
    request: ExperienceTestRunRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation to execute this test harness is required.")
    try:
        item = experience.get_experience(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    if item.get("status") == "verified":
        raise HTTPException(status_code=409, detail="Verified experiences are immutable; create a new experience to retest.")
    if not item.get("source_evidence"):
        raise HTTPException(status_code=409, detail="Experience needs source evidence before a test can run.")
    if not item.get("test_plan"):
        raise HTTPException(status_code=409, detail="Experience needs a test plan before a test can run.")
    if not any(
        isinstance(entry, dict)
        and isinstance(entry.get("test"), str)
        and entry["test"].strip() == request.test_name.strip()
        for entry in item["test_plan"]
    ):
        raise HTTPException(status_code=422, detail="test_name must exactly match an item in the saved test plan.")
    try:
        experience_tasks.validate_test_harness(request.source_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not sandbox_consent.is_enabled():
        raise HTTPException(status_code=409, detail="Sandbox execution requires explicit, current user consent.")
    try:
        configured_handler = experience_tasks.handler_from_environment()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="The controlled sandbox runner is misconfigured.") from exc
    if configured_handler is None:
        raise HTTPException(status_code=503, detail="The controlled sandbox runner is not configured.")
    try:
        task = task_manager.create_task(
            experience_tasks.EXPERIENCE_TEST_TASK_TYPE,
            {
                "experience_id": experience_id,
                "test_name": request.test_name.strip(),
                "source_code": request.source_code,
                "timeout_seconds": request.timeout_seconds,
                "max_output_bytes": request.max_output_bytes,
            },
            priority=5,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience test could not be queued.") from exc
    return {
        "task": task,
        "message": "Controlled test queued. Verification depends on the actual sandbox result and saved source/test evidence.",
    }


@router.post("/{experience_id}/feedback")
def set_experience_feedback(
    experience_id: int,
    request: ExperienceFeedbackRequest,
) -> dict[str, Any]:
    try:
        feedback = experience_feedback.set_feedback(experience_id, request.rating)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"feedback": feedback, "changes_verification": False}


@router.get("/{experience_id}/feedback")
def get_experience_feedback(experience_id: int) -> dict[str, Any]:
    try:
        item = experience.get_experience(experience_id)
        feedback = experience_feedback.get_feedback_map([experience_id]).get(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    return {"experience_id": experience_id, "feedback": feedback, "status": item.get("status")}


@router.delete("/{experience_id}/feedback")
def delete_experience_feedback(experience_id: int) -> dict[str, Any]:
    try:
        deleted = experience_feedback.delete_feedback(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    return {"experience_id": experience_id, "deleted": deleted}


@router.post("/{experience_id}/refresh-sources")
def refresh_experience_sources(
    experience_id: int,
    request: ExperienceSourceRefreshRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation to fetch saved source URLs is required.")
    try:
        return experience_source_checks.refresh_experience_sources(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience source revalidation could not be completed.") from exc


@router.get("/{experience_id}/source-checks")
def get_experience_source_checks(
    experience_id: int,
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    try:
        checks = experience_source_checks.list_source_checks(experience_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Experience source-check history is unavailable.") from exc
    return {"experience_id": experience_id, "checks": checks, "count": len(checks)}


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
    changes = (
        request.model_dump(exclude_unset=True)
        if hasattr(request, "model_dump")
        else request.dict(exclude_unset=True)
    )
    if not changes or all(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="At least one editable field is required.")
    try:
        item = experience.update_experience(experience_id, **changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experience not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "controlled experience runner" in str(exc) else 422, detail=str(exc)) from exc
    return {"experience": item}
