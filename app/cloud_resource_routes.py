"""Loopback-only API for viewing and triggering cloud resource discovery."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource discovery storage is unavailable.") from exc


@router.post("/discover", status_code=status.HTTP_202_ACCEPTED)
def request_cloud_discovery() -> dict[str, Any]:
    try:
        task = cloud_resources.enqueue_discovery_task(manual=True)
        return {"task": task, "message": "Cloud resource discovery queued; candidates remain unreviewed until verified."}
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource discovery could not be queued.") from exc



class CandidateReviewRequest(BaseModel):
    expected_content_sha256: str = Field(min_length=64, max_length=64)
    review_status: str = Field(min_length=1, max_length=20)
    availability_status: str = Field(default="unknown", min_length=1, max_length=30)
    quota: dict[str, Any] = Field(default_factory=dict)
    terms_url: str | None = Field(default=None, max_length=2048)


@router.patch("/{candidate_id}/review")
def review_cloud_resource(
    candidate_id: int,
    request: CandidateReviewRequest,
) -> dict[str, Any]:
    try:
        candidate = cloud_resources.review_candidate(
            candidate_id,
            expected_content_sha256=request.expected_content_sha256,
            review_status=request.review_status,
            availability_status=request.availability_status,
            quota=request.quota,
            terms_url=request.terms_url,
        )
        return {"candidate": candidate}
    except cloud_resources.CloudResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except cloud_resources.CloudResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (cloud_resources.CloudResourceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource review storage is unavailable.") from exc



class ClaimReviewRequest(BaseModel):
    expected_source_content_sha256: str = Field(min_length=64, max_length=64)
    review_status: str = Field(min_length=1, max_length=20)


@router.patch("/{candidate_id}/claims/{claim_id}/review")
def review_cloud_resource_claim(
    candidate_id: int,
    claim_id: int,
    request: ClaimReviewRequest,
) -> dict[str, Any]:
    try:
        claim = cloud_resources.review_claim(
            candidate_id,
            claim_id,
            expected_source_content_sha256=request.expected_source_content_sha256,
            review_status=request.review_status,
        )
        return {"claim": claim}
    except cloud_resources.CloudResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except cloud_resources.CloudResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (cloud_resources.CloudResourceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource claim review storage is unavailable.") from exc
