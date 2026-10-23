"""Read-only and review-plan APIs for staged learning candidates.

A structurally accepted lesson remains temporary and unverified. This module
never promotes candidates or treats a proposed test plan as an executed test.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .db import learning_connection
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/candidates",
    tags=["learning-candidates"],
    dependencies=[Depends(require_loopback_client)],
)


class CandidateTestPlanItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=10, max_length=1000)
    expected_result: str = Field(min_length=10, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class CandidateTestPlanRequest(BaseModel):
    confirm: StrictBool
    oracle_reviewed: StrictBool
    review_note: str = Field(min_length=20, max_length=1000)
    tests: list[CandidateTestPlanItem] = Field(min_length=1, max_length=10)

    model_config = ConfigDict(extra="forbid")


def _decode_json(value: str, *, field: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Stored candidate {field} is invalid.") from exc


def _candidate(row: sqlite3.Row, *, include_content: bool = False) -> dict[str, Any]:
    result = dict(row)
    content = result.pop("content")
    expected_hash = result.get("content_sha256")
    actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result["integrity_valid"] = (
        isinstance(expected_hash, str) and actual_hash == expected_hash
    )
    result["source_urls"] = _decode_json(result.pop("source_urls_json"), field="source URLs")
    result["quality_report"] = _decode_json(
        result.pop("quality_report_json"), field="quality report"
    )
    result["test_plan"] = _decode_json(result.pop("test_plan_json"), field="test plan")
    result["test_results"] = _decode_json(
        result.pop("test_results_json"), field="test results"
    )
    if include_content:
        if not result["integrity_valid"]:
            raise HTTPException(status_code=409, detail="Candidate content hash mismatch; review is blocked.")
        result["content"] = content
    else:
        result["content_preview"] = content[:600]
    return result


_COLUMNS = """
    id, job_id, topic, content, source_kind, source_urls_json, content_sha256,
    quality_report_json, status, test_plan_json, test_results_json, failure_reason,
    promoted_knowledge_id, created_at, updated_at
"""


@router.get("")
def list_learning_candidates(
    status: Literal["awaiting_test", "passed", "failed"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    try:
        with learning_connection() as connection:
            if status is None:
                rows = connection.execute(
                    f"SELECT {_COLUMNS} FROM temporary_knowledge_candidates ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_COLUMNS} FROM temporary_knowledge_candidates WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        items = [_candidate(row) for row in rows]
        return {"items": items, "count": len(items), "limit": limit, "status": status}
    except HTTPException:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Learning candidates are unavailable.") from exc


@router.get("/{candidate_id}")
def get_learning_candidate(candidate_id: int) -> dict[str, Any]:
    try:
        with learning_connection() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Learning candidate not found.")
        return {"candidate": _candidate(row, include_content=True)}
    except HTTPException:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Learning candidate is unavailable.") from exc


@router.post("/{candidate_id}/test-plan", status_code=201)
def save_candidate_test_plan(
    candidate_id: int,
    request: CandidateTestPlanRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required.")
    if request.oracle_reviewed is not True:
        raise HTTPException(
            status_code=422,
            detail="A human must attest that the proposed tests use an independent oracle.",
        )
    plan = {
        "version": 1,
        "oracle_reviewed": True,
        "review_note": request.review_note.strip(),
        "tests": [item.model_dump() for item in request.tests],
    }
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Learning candidate not found.")
            candidate = _candidate(row, include_content=True)
            if candidate["status"] != "awaiting_test":
                raise HTTPException(status_code=409, detail="Only candidates awaiting tests can receive a test plan.")
            existing = candidate["test_plan"]
            if existing not in ([], plan):
                raise HTTPException(
                    status_code=409,
                    detail="A test plan is already recorded; replacing it requires an audited revision workflow.",
                )
            if existing == []:
                cursor = connection.execute(
                    """UPDATE temporary_knowledge_candidates
                       SET test_plan_json=?, updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND status='awaiting_test' AND test_plan_json='[]'
                         AND test_results_json='[]'""",
                    (encoded, candidate_id),
                )
                if cursor.rowcount != 1:
                    raise HTTPException(status_code=409, detail="Candidate changed while saving the test plan.")
        return {
            "candidate_id": candidate_id,
            "test_plan": plan,
            "executed": False,
            "promoted": False,
            "note": "Saved as a proposal only; this endpoint does not execute tests or promote knowledge.",
        }
    except HTTPException:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Candidate test plan could not be saved.") from exc
