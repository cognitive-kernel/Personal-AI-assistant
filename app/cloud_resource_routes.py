"""Loopback-only API for viewing and triggering cloud resource discovery."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StrictBool, StrictStr

from . import cloud_resource_planner, cloud_resources
from .kaggle_quota_client import KaggleQuotaClientError, fetch_quota_snapshot
from .oci_quota_client import OciQuotaClientError, fetch_service_limits
from .task_routes import require_loopback_client
from .pagination import metadata as pagination_metadata

router = APIRouter(
    prefix="/api/cloud-resources",
    tags=["cloud-resources"],
    dependencies=[Depends(require_loopback_client)],
)


@router.get("")
def list_cloud_resources(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> dict[str, Any]:
    size = limit if limit is not None else page_size
    try:
        candidates = cloud_resources.list_candidates(limit=size + 1, offset=(page - 1) * size)
        has_next = len(candidates) > size
        return {
            "latest_run": cloud_resources.latest_discovery_run(),
            "candidates": candidates[:size],
            "pagination": {"page": page, "page_size": size, "total": None, "pages": None,
                           "has_previous": page > 1, "has_next": has_next},
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



class ResourcePlanningRequest(BaseModel):
    min_cpu_count: Any = 1
    min_memory_gib: Any = 0
    require_gpu: StrictBool = False
    gpu_models: list[StrictStr] = Field(default_factory=list, max_length=20)
    allowed_provider_domains: list[StrictStr] = Field(default_factory=list, max_length=100)


@router.get("/plans")
def list_resource_selection_plans(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return {
            "plans": cloud_resources.list_resource_selection_plans(limit=limit),
            "execution_authorized": False,
            "remote_execution_enabled": False,
        }
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Resource plan history is unavailable.") from exc


@router.get("/plans/{plan_id}")
def get_resource_selection_plan(plan_id: int) -> dict[str, Any]:
    try:
        return cloud_resources.get_resource_selection_plan(plan_id)
    except cloud_resources.CloudResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Resource plan history is unavailable.") from exc


@router.post("/plan")
def plan_cloud_resource_selection(request: ResourcePlanningRequest) -> dict[str, Any]:
    """Return a loopback-only dry-run resource plan; never starts remote execution."""
    try:
        candidates = cloud_resources.list_candidates(limit=500)
        plan = cloud_resource_planner.build_selection_plan(
            candidates,
            {
                "min_cpu_count": request.min_cpu_count,
                "min_memory_gib": request.min_memory_gib,
                "require_gpu": request.require_gpu,
                "gpu_models": request.gpu_models,
                "allowed_provider_domains": request.allowed_provider_domains,
            },
        )
        return cloud_resources.save_resource_selection_plan(plan)
    except cloud_resource_planner.ResourcePlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Cloud resource planning is unavailable.") from exc


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


class ProviderQuotaConsentRequest(BaseModel):
    enabled: StrictBool
    user_consent: StrictBool
    executable_path: str = Field(default="", max_length=2048)


@router.get("/kaggle-quota")
def get_kaggle_quota_status() -> dict[str, Any]:
    try:
        return {
            "consent": cloud_resources.get_provider_consent(),
            "snapshots": cloud_resources.list_provider_quota_snapshots(),
            "consent_events": cloud_resources.list_provider_consent_events(limit=20),
        }
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Kaggle quota status storage is unavailable.") from exc


@router.patch("/kaggle-quota/consent")
def update_kaggle_quota_consent(request: ProviderQuotaConsentRequest) -> dict[str, Any]:
    try:
        consent = cloud_resources.record_provider_consent(
            enabled=request.enabled,
            user_consent=request.user_consent,
            executable_path=request.executable_path,
        )
        return {
            "consent": consent,
            "snapshots": cloud_resources.list_provider_quota_snapshots(),
            "consent_events": cloud_resources.list_provider_consent_events(limit=20),
        }
    except cloud_resources.CloudResourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Kaggle quota consent storage is unavailable.") from exc


@router.post("/kaggle-quota/refresh")
def refresh_kaggle_quota() -> dict[str, Any]:
    try:
        consent = cloud_resources.get_provider_consent()
        if not consent["enabled"] or not consent["user_consent"]:
            raise HTTPException(
                status_code=409,
                detail="Kaggle quota access is disabled; enable it with explicit consent first.",
            )
        snapshot = fetch_quota_snapshot(
            executable_path=consent["executable_path"],
            enabled=consent["enabled"],
            user_consent=consent["user_consent"],
        )
        snapshots = cloud_resources.save_provider_quota_snapshot(snapshot)
        return {
            "consent": cloud_resources.get_provider_consent(),
            "snapshots": snapshots,
            "consent_events": cloud_resources.list_provider_consent_events(limit=20),
        }
    except HTTPException:
        raise
    except KaggleQuotaClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except cloud_resources.CloudResourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Kaggle quota refresh failed safely.") from exc



class OciLimitConsentRequest(BaseModel):
    enabled: StrictBool
    user_consent: StrictBool
    executable_path: str = Field(default="", max_length=2048)
    config_file: str = Field(default="", max_length=2048)
    profile: str = Field(default="", max_length=64)
    region: str = Field(default="", max_length=80)
    compartment_id: str = Field(default="", max_length=512)


@router.get("/oci-limits")
def get_oci_limits_status() -> dict[str, Any]:
    try:
        return {
            "consent": cloud_resources.get_oci_limit_consent(),
            "snapshots": cloud_resources.list_oci_limit_snapshots(),
            "consent_events": cloud_resources.list_oci_limit_consent_events(limit=20),
            "execution_authorized": False,
        }
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="OCI limit status storage is unavailable.") from exc


@router.patch("/oci-limits/consent")
def update_oci_limit_consent(request: OciLimitConsentRequest) -> dict[str, Any]:
    try:
        consent = cloud_resources.record_oci_limit_consent(
            enabled=request.enabled,
            user_consent=request.user_consent,
            executable_path=request.executable_path,
            config_file=request.config_file,
            profile=request.profile,
            region=request.region,
            compartment_id=request.compartment_id,
        )
        return {
            "consent": consent,
            "snapshots": cloud_resources.list_oci_limit_snapshots(),
            "consent_events": cloud_resources.list_oci_limit_consent_events(limit=20),
            "execution_authorized": False,
        }
    except cloud_resources.CloudResourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="OCI limit consent storage is unavailable.") from exc


@router.post("/oci-limits/refresh")
def refresh_oci_limits() -> dict[str, Any]:
    try:
        consent = cloud_resources.get_oci_limit_consent()
        if not consent["enabled"] or not consent["user_consent"]:
            raise HTTPException(
                status_code=409,
                detail="OCI limit access is disabled; enable it with explicit consent first.",
            )
        snapshot = fetch_service_limits(
            executable_path=consent["executable_path"],
            config_file=consent["config_file"],
            profile=consent["profile"],
            region=consent["region"],
            compartment_id=consent["compartment_id"],
            enabled=consent["enabled"],
            user_consent=consent["user_consent"],
        )
        snapshots = cloud_resources.save_oci_limit_snapshot(snapshot)
        return {
            "consent": cloud_resources.get_oci_limit_consent(),
            "snapshots": snapshots,
            "consent_events": cloud_resources.list_oci_limit_consent_events(limit=20),
            "observed_at": snapshot.observed_at.isoformat(),
            "execution_authorized": False,
        }
    except HTTPException:
        raise
    except OciQuotaClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except cloud_resources.CloudResourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="OCI limit refresh failed safely.") from exc
