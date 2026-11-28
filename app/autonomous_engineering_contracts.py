"""Stable, dependency-light contracts for the Autonomous Engineering API.

This module owns request validation and shared protocol constants only. It must
not import the FastAPI router, provider adapters, persistence, or task state.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr


HTTP_ERROR_CODES = {
    400: "AUTONOMOUS_ENGINEERING_BAD_REQUEST",
    401: "AUTONOMOUS_ENGINEERING_UNAUTHORIZED",
    403: "AUTONOMOUS_ENGINEERING_FORBIDDEN",
    404: "AUTONOMOUS_ENGINEERING_NOT_FOUND",
    408: "AUTONOMOUS_ENGINEERING_TIMEOUT",
    409: "AUTONOMOUS_ENGINEERING_CONFLICT",
    422: "AUTONOMOUS_ENGINEERING_VALIDATION_FAILED",
    429: "AUTONOMOUS_ENGINEERING_RATE_LIMITED",
    500: "AUTONOMOUS_ENGINEERING_INTERNAL_ERROR",
    502: "AUTONOMOUS_ENGINEERING_UPSTREAM_ERROR",
    503: "AUTONOMOUS_ENGINEERING_UNAVAILABLE",
    504: "AUTONOMOUS_ENGINEERING_TIMEOUT",
}
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
ACTIVE_RUN_STATES = ("queued", "in_progress", "waiting", "requested", "pending", "suspended")
CORRELATION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
CODEX_CONTEXT_VARIANT_PATTERN = re.compile(
    r"-codex-ctx(?:[0-9]+)?(?:-paa-[0-9]+-[0-9]+-[a-f0-9]{12})?$",
    re.IGNORECASE,
)


class EngineeringSettings(BaseModel):
    model: StrictStr
    interval_hours: StrictInt
    execution_mode: StrictStr = "continuous"


class ManualTaskRequest(BaseModel):
    task: StrictStr = Field(default="", max_length=2000)


class ClearEngineeringQueueRequest(BaseModel):
    confirm: StrictBool


class ModelPullRequest(BaseModel):
    model: StrictStr = Field(min_length=1, max_length=120)


class CancelRequest(BaseModel):
    run_id: StrictInt = Field(gt=0)
