"""Loopback-only, consent-gated API for testing sourced Python factual claims."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import factual_verification_tasks, sandbox_consent, sandbox_tasks, task_manager
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/factual-knowledge",
    tags=["factual-knowledge"],
    dependencies=[Depends(require_loopback_client)],
)


class FactualClaimTestRequest(BaseModel):
    confirm: StrictBool
    oracle_reviewed: StrictBool
    oracle_review_note: str = Field(min_length=20, max_length=1000)
    test_name: str = Field(min_length=1, max_length=300)
    source_code: str = Field(min_length=1, max_length=65536)
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=120)
    max_output_bytes: StrictInt = Field(default=32768, ge=1, le=65536)
    priority: StrictInt = Field(default=0, ge=-100000, le=100000)

    model_config = ConfigDict(extra="forbid")


def _configured() -> bool:
    try:
        return sandbox_tasks.handler_from_environment() is not None
    except ValueError:
        return False


@router.post("/{knowledge_id}/run-test", status_code=status.HTTP_202_ACCEPTED)
def queue_factual_claim_test(
    knowledge_id: int,
    request: FactualClaimTestRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation to execute this test harness is required.")
    if request.oracle_reviewed is not True:
        raise HTTPException(status_code=422, detail="Explicit attestation that the test matches an independent oracle is required.")
    try:
        factual_verification_tasks.get_verification_candidate(knowledge_id)
        factual_verification_tasks.validate_test_harness(request.source_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Factual claim not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        consent = sandbox_consent.get_consent()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent storage is unavailable.") from exc
    if not consent["enabled"]:
        raise HTTPException(status_code=409, detail="Factual claim testing requires explicit current sandbox consent.")
    if not _configured():
        raise HTTPException(status_code=409, detail="Factual claim testing requires a configured digest-pinned Docker sandbox.")

    source_hash = hashlib.sha256(request.source_code.encode("utf-8")).hexdigest()
    payload = {
        "knowledge_id": knowledge_id,
        "oracle_reviewed": request.oracle_reviewed,
        "oracle_review_note": request.oracle_review_note.strip(),
        "test_name": request.test_name.strip(),
        "source_code": request.source_code,
        "timeout_seconds": request.timeout_seconds,
        "max_output_bytes": request.max_output_bytes,
    }
    try:
        task = task_manager.create_task(
            factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE,
            payload,
            priority=request.priority,
            max_attempts=1,
            dedupe_key=f"factual-verify:{knowledge_id}:{source_hash}:{request.test_name.strip()}",
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual claim test could not be queued.") from exc
    return {
        "task": task,
        "message": "Factual claim test queued in the isolated sandbox; any L2 promotion requires the reviewed independent oracle and an exact Python-version match.",
    }
