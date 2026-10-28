"""Loopback-only API for the evidence-bounded learner capability map."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt

from . import learning_capabilities, learning_practical, sandbox_consent, sandbox_tasks, task_manager
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


class PracticalSubmission(BaseModel):
    job_id: StrictInt = Field(gt=0)
    source_code: str = Field(min_length=1, max_length=32768)


def _practical_sandbox_available() -> bool:
    try:
        return sandbox_tasks.handler_from_environment() is not None
    except ValueError:
        return False


@router.get("/practical/exercises")
def list_practical_exercises(job_id: int | None = None) -> dict[str, Any]:
    exercises = learning_practical.list_exercises()
    if job_id is not None:
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
            raise HTTPException(status_code=422, detail="job_id must be a positive integer.")
        eligible = []
        for exercise in exercises:
            try:
                learning_practical.validate_practical_job(job_id, exercise["id"])
            except (KeyError, ValueError):
                continue
            eligible.append(exercise)
        exercises = eligible
    return {
        "exercises": exercises,
        "execution_requires_explicit_sandbox_consent": True,
        "grading_is_deterministic": True,
    }


@router.post("/practical/exercises/{exercise_id}/submit", status_code=202)
def submit_practical_exercise(exercise_id: str, request: PracticalSubmission) -> dict[str, Any]:
    if (
        not request.source_code.strip() or "\\x00" in request.source_code
        or len(request.source_code.encode("utf-8")) > 32 * 1024
    ):
        raise HTTPException(status_code=422, detail="Practical source must be non-empty, NUL-free, and at most 32 KiB.")
    try:
        learning_practical.validate_practical_job(request.job_id, exercise_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning job or practical exercise was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        consent = sandbox_consent.get_consent()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent storage is unavailable.") from exc
    if not consent["enabled"]:
        raise HTTPException(status_code=409, detail="Practical grading requires explicit sandbox consent.")
    if not _practical_sandbox_available():
        raise HTTPException(status_code=409, detail="Practical grading requires a configured Docker sandbox.")

    try:
        task = task_manager.create_task(
            sandbox_tasks.SANDBOX_TASK_TYPE,
            {
                "source_code": request.source_code,
                "timeout_seconds": 15,
                "max_output_bytes": 8192,
                "learning_practical": {"exercise_id": exercise_id, "job_id": request.job_id},
            },
            priority=0,
            max_attempts=1,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Practical assessment could not be queued.") from exc
    return {
        "task": task,
        "message": "Practical assessment queued in the consent-gated Docker sandbox.",
    }
