"""Local diagnostics and explicit controls for the Ollama-backed GitHub engineering agent."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, StrictInt, StrictStr

from .task_routes import require_loopback_client
from .autonomous_engineering_progress import (
    cleanup_shared_progress_data,
    get_progress,
    pending_work_status,
    read_run_diagnostics,
    storage_accessible,
)
from .ollama_context import maximum_context_tokens, resolve_model_architecture, supports_tool_calling
from .autonomous_engineering_state import durable_state_lock, execution_state_directory
from .autonomous_engineering_checkpoint_lock import checkpoint_store_lock
from .autonomous_engineering_execution_lock import repository_execution_lock
from .autonomous_engineering_model_pull import get_model_pull, start_model_pull

_RUN_PROGRESS_CACHE: dict[str, Any] = {"run_id": None, "at": 0.0, "value": None}
_STATUS_PROBE_CONTEXT = threading.local()
_STATUS_PROBE_TIMEOUT_SECONDS = 35.0
_GITHUB_STATUS_PROBE_TIMEOUT_SECONDS = 20.0
_STATUS_EXECUTOR_MAX_WORKERS = 5
_STATUS_EXECUTOR = ThreadPoolExecutor(
    max_workers=_STATUS_EXECUTOR_MAX_WORKERS,
    thread_name_prefix="autonomous-status",
)
# Bound running plus queued probes, not just the worker count. Otherwise requests
# can accumulate an unbounded executor queue while earlier probes are stalled.
_STATUS_EXECUTOR_SLOTS = threading.BoundedSemaphore(_STATUS_EXECUTOR_MAX_WORKERS)
_STATUS_LOGGER = logging.getLogger(__name__)

# Serialize UI-originated workflow dispatches within the local API process. The
# durable pending-task marker and GitHub workflow concurrency remain the
# cross-process/restart safety layer.
_DISPATCH_REQUEST_LOCK = threading.Lock()
_DISPATCH_VISIBILITY_GRACE_SECONDS = 15.0
_LAST_DISPATCH_ACCEPTED_AT = 0.0
_LAST_RESUME_ACCEPTED_AT = 0.0

_HTTP_ERROR_CODES = {
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
_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_CORRELATION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def _structured_error_response(
    *, status_code: int, message: str, detail: Any, headers: dict[str, str] | None = None,
    code: str | None = None, retryable: bool | None = None, safe_details: dict[str, Any] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    correlation_id = response_headers.get("X-Correlation-ID") or uuid.uuid4().hex
    error_code = response_headers.get("X-Error-Code") or code or _HTTP_ERROR_CODES.get(
        status_code, "AUTONOMOUS_ENGINEERING_HTTP_ERROR"
    )
    retry_header = response_headers.get("X-Retryable")
    can_retry = (
        retry_header.casefold() == "true" if retry_header is not None
        else (status_code in _RETRYABLE_HTTP_STATUSES if retryable is None else retryable)
    )
    response_headers.update({
        "X-Correlation-ID": correlation_id,
        "X-Error-Code": error_code,
        "X-Retryable": "true" if can_retry else "false",
    })
    _STATUS_LOGGER.warning(
        "Autonomous Engineering API error; status_code=%s code=%s retryable=%s correlation_id=%s",
        status_code,
        error_code,
        can_retry,
        correlation_id,
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": error_code,
            "message": message,
            "correlation_id": correlation_id,
            "retryable": can_retry,
            "safe_details": safe_details if isinstance(safe_details, dict) else {},
        },
        headers=response_headers,
    )


class AutonomousEngineeringAPIRoute(APIRoute):
    """Keep all Autonomous Engineering API failures on one safe diagnostic contract."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def structured_handler(request):
            supplied_id = str(request.headers.get("X-Correlation-ID") or "").strip().lower()
            correlation_id = supplied_id if _CORRELATION_ID_PATTERN.fullmatch(supplied_id) else uuid.uuid4().hex
            request.state.correlation_id = correlation_id
            try:
                response = await original_handler(request)
            except HTTPException as exc:
                message = exc.detail if isinstance(exc.detail, str) else "درخواست ناموفق بود."
                headers = dict(exc.headers or {})
                headers.setdefault("X-Correlation-ID", correlation_id)
                return _structured_error_response(
                    status_code=exc.status_code,
                    message=message,
                    detail=message,
                    headers=headers,
                    safe_details=getattr(exc, "safe_details", None),
                )
            except RequestValidationError as exc:
                errors = []
                for error in exc.errors():
                    location = error.get("loc", ())
                    errors.append({
                        "loc": [str(part) for part in location if isinstance(part, (str, int))],
                        "msg": str(error.get("msg") or "Invalid value"),
                        "type": str(error.get("type") or "validation_error"),
                    })
                return _structured_error_response(
                    status_code=422,
                    message="ورودی درخواست معتبر نیست.",
                    detail=errors,
                    headers={"X-Correlation-ID": correlation_id},
                    code="AUTONOMOUS_ENGINEERING_VALIDATION_FAILED",
                    retryable=False,
                    safe_details={"validation_errors": errors},
                )
            except Exception as exc:
                _STATUS_LOGGER.error(
                    "Autonomous Engineering API failed; exception_type=%s correlation_id=%s",
                    type(exc).__name__,
                    correlation_id,
                )
                return _structured_error_response(
                    status_code=500,
                    message="خطای داخلی سرویس رخ داد؛ شناسهٔ پیگیری را برای بررسی ارائه کنید.",
                    detail="خطای داخلی سرویس رخ داد.",
                    headers={"X-Correlation-ID": correlation_id},
                    code="AUTONOMOUS_ENGINEERING_INTERNAL_ERROR",
                    retryable=False,
                    safe_details={"exception_type": type(exc).__name__},
                )
            response.headers.setdefault("X-Correlation-ID", correlation_id)
            return response

        return structured_handler


def _is_windows() -> bool:
    """Expose the platform boundary so cleanup behavior can be tested portably."""
    return os.name == "nt"


def _storage_and_pending_work_status() -> dict[str, Any]:
    """Read broker health and durable work state in one bounded executor slot."""
    return {
        "accessible": storage_accessible(),
        "pending_work": pending_work_status(),
    }


router = APIRouter(
    prefix="/api/autonomous-engineering",
    tags=["autonomous-engineering"],
    route_class=AutonomousEngineeringAPIRoute,
    dependencies=[Depends(require_loopback_client)],
)

_DEFAULT_REPOSITORY = "cognitive-kernel/Personal-AI-assistant"
_WORKFLOW = "autonomous-codex-engineering.yml"
_DEFAULT_INTERVAL_HOURS = 24
_ALLOWED_INTERVAL_HOURS = {1, 2, 4, 6, 12, 24}
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_CODEX_CONTEXT_VARIANT_PATTERN = re.compile(r"-codex-ctx(?:[0-9]+)?$", re.IGNORECASE)


def _is_codex_context_variant(model: str) -> bool:
    return bool(_CODEX_CONTEXT_VARIANT_PATTERN.search(model.strip()))


class EngineeringSettings(BaseModel):
    model: StrictStr
    interval_hours: StrictInt
    execution_mode: StrictStr = "continuous"


class ManualTaskRequest(BaseModel):
    task: StrictStr = Field(default="", max_length=2000)


class ModelPullRequest(BaseModel):
    model: StrictStr = Field(min_length=1, max_length=120)


def _repository() -> str:
    value = os.getenv("AUTONOMOUS_ENGINEERING_REPOSITORY", _DEFAULT_REPOSITORY).strip()
    if "/" not in value or any(part in value for part in (" ", "\\", "..")):
        return _DEFAULT_REPOSITORY
    owner, name = value.split("/", 1)
    return value if owner and name and "/" not in name else _DEFAULT_REPOSITORY


def _run(
    command: list[str], timeout: int = 8, *, raise_os_error: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        if raise_os_error:
            raise
        return None



# Service implementations live in a separate module; wrappers preserve the public API and test seams.
from . import autonomous_engineering_services as _services


_NoRedirectHandler = _services.NoRedirectHandler


def _ollama_status(model: str) -> dict[str, Any]:
    from .ollama import (
        _find_running_model_entry,
        _model_memory_estimate,
        _recommended_context_tokens,
        _request as ollama_request,
    )
    from .hardware import detect_hardware

    def _recommended_context(
        selected_model: str,
        model_info: dict[str, Any],
        details: dict[str, Any],
        model_entry: dict[str, Any],
    ) -> int | None:
        # Reuse /api/show and /api/tags data already fetched by ollama_status.
        # model_details() repeats both requests and adds a separate /api/ps call,
        # which made each dashboard refresh unnecessarily slow.
        running_payload = ollama_request("/api/ps", timeout=4)
        running = running_payload.get("models", []) if isinstance(running_payload, dict) else []
        running_entry = _find_running_model_entry(running, selected_model)
        architecture = resolve_model_architecture(model_info, details.get("family"))
        maximum = maximum_context_tokens(model_info, architecture)
        estimate = _model_memory_estimate(
            selected_model, model_info, details, model_entry.get("size"), maximum
        )
        return _recommended_context_tokens(
            maximum,
            estimate,
            detect_hardware(),
            model_is_loaded=running_entry is not None,
            running_context_tokens=(
                running_entry.get("context_length") if isinstance(running_entry, dict) else None
            ),
        )

    def _tool_probe(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            running = ollama_request("/api/ps", timeout=4).get("models", [])
            if isinstance(running, list) and _find_running_model_entry(running, str(payload.get("model") or "")) is None:
                payload["keep_alive"] = "0"
        except Exception:
            # Unknown residency is not permission to unload a model.
            pass
        return ollama_request("/api/chat", payload, timeout=45)

    return _services.ollama_status(
        model,
        build_opener=urllib.request.build_opener,
        maximum_context_fn=maximum_context_tokens,
        supports_tool_calling_fn=supports_tool_calling,
        is_context_variant=_is_codex_context_variant,
        recommended_context_details_fn=_recommended_context,
        tool_probe_fn=_tool_probe,
        allow_live_tool_probe=getattr(_STATUS_PROBE_CONTEXT, "allow_live_tool_probe", True),
    )


def _codex_status() -> dict[str, Any]:
    return _services.codex_status(shutil.which, _run)


def _runner_status() -> dict[str, Any]:
    return _services.runner_status(psutil.process_iter)


def _github_snapshot() -> dict[str, Any]:
    return _services.github_snapshot(
        run_command=_run,
        which=shutil.which,
        repository=_repository(),
        workflow=_WORKFLOW,
        default_interval_hours=_DEFAULT_INTERVAL_HOURS,
        allowed_interval_hours=_ALLOWED_INTERVAL_HOURS,
        model_pattern=_MODEL_PATTERN,
    )


def _submit_status_probe(function):
    """Submit a probe only when a bounded worker/queue slot is available."""
    if not _STATUS_EXECUTOR_SLOTS.acquire(blocking=False):
        return None
    try:
        future = _STATUS_EXECUTOR.submit(function)
    except RuntimeError:
        _STATUS_EXECUTOR_SLOTS.release()
        _STATUS_LOGGER.exception("Autonomous Engineering status probe could not be scheduled")
        return None
    except Exception:
        _STATUS_EXECUTOR_SLOTS.release()
        raise
    future.add_done_callback(lambda _future: _STATUS_EXECUTOR_SLOTS.release())
    return future


def _resolve_status_probe(
    future,
    name: str,
    fallback: Any,
    timed_out: list[str],
    errors: list[str],
    unavailable: list[str],
) -> Any:
    if future is None:
        unavailable.append(name)
        _STATUS_LOGGER.warning(
            "Autonomous Engineering status probe skipped because capacity is exhausted: %s",
            name,
        )
        return fallback
    if not future.done():
        timed_out.append(name)
        _STATUS_LOGGER.warning("Autonomous Engineering status probe timed out: %s", name)
        return fallback
    try:
        return future.result()
    except Exception as exc:
        errors.append(name)
        _STATUS_LOGGER.exception(
            "Autonomous Engineering status probe failed: %s (%s)",
            name,
            type(exc).__name__,
        )
        return fallback


def _status_ollama_fallback(model: str) -> dict[str, Any]:
    return {
        "online": False,
        "model": model,
        "model_installed": False,
        "model_selectable": False,
        "model_is_codex_context_variant": _is_codex_context_variant(model),
        "models": [],
        "selectable_models": [],
        "model_context_length": None,
        "model_recommended_context_length": None,
        "model_capabilities": [],
        "model_tools_metadata_available": False,
        "model_tools_verification_method": "unverified",
        "model_supports_tools": False,
        "model_metadata_available": False,
        "model_context_supported": False,
        "model_context_error": "The Ollama status probe did not finish within the request budget.",
        "model_metadata_error": "The Ollama status probe did not finish within the request budget.",
    }


def collect_status() -> dict[str, Any]:
    # A bounded pool plus non-blocking admission control prevents stalled probes
    # from creating an unbounded queue across concurrent status requests.
    deadline = time.monotonic() + _STATUS_PROBE_TIMEOUT_SECONDS
    timed_out: list[str] = []
    errors: list[str] = []
    unavailable: list[str] = []

    github_future = _submit_status_probe(_github_snapshot)
    codex_future = _submit_status_probe(_codex_status)
    runner_future = _submit_status_probe(_runner_status)
    storage_future = _submit_status_probe(_storage_and_pending_work_status)

    # GitHub variables determine the selected model. Bound this dependency
    # separately, then use the remaining request budget for every other probe.
    github_wait = min(
        _GITHUB_STATUS_PROBE_TIMEOUT_SECONDS,
        max(0.0, deadline - time.monotonic()),
    )
    if github_future is not None:
        wait((github_future,), timeout=github_wait)
    github_fallback = {
        "cli_installed": bool(shutil.which("gh")),
        "authenticated": False,
        "recent_runs": [],
        "runs_available": False,
        "settings": {
            "model": "",
            "interval_hours": _DEFAULT_INTERVAL_HOURS,
            "execution_mode": "continuous",
            "paused": False,
        },
    }
    github = _resolve_status_probe(
        github_future, "github", github_fallback, timed_out, errors, unavailable
    )
    if not isinstance(github, dict):
        errors.append("github")
        github = github_fallback
    github_settings = github.get("settings", {})
    if not isinstance(github_settings, dict):
        errors.append("github_settings")
        github_settings = {}
    model = github_settings.get("model", "")
    if not isinstance(model, str):
        model = ""

    def _status_ollama() -> dict[str, Any]:
        # The probe policy is thread-local, so set it in the worker that performs
        # the Ollama check rather than in the request thread.
        _STATUS_PROBE_CONTEXT.allow_live_tool_probe = False
        try:
            return _ollama_status(model)
        finally:
            del _STATUS_PROBE_CONTEXT.allow_live_tool_probe

    ollama_future = _submit_status_probe(_status_ollama)
    remaining = max(0.0, deadline - time.monotonic())
    futures = tuple(
        future
        for future in (codex_future, runner_future, storage_future, ollama_future)
        if future is not None
    )
    if futures:
        wait(futures, timeout=remaining)

    codex = _resolve_status_probe(
        codex_future, "codex", {"installed": False, "version": None},
        timed_out, errors, unavailable,
    )
    runner = _resolve_status_probe(
        runner_future, "runner", {"running": False, "process_detected": False},
        timed_out, errors, unavailable,
    )
    shared_state = _resolve_status_probe(
        storage_future, "storage",
        {"accessible": False, "pending_work": {"available": False, "blocked": True, "checkpoint": {"available": True, "verified": False, "reason": "status_unavailable"}}},
        timed_out, errors, unavailable,
    )
    if not isinstance(shared_state, dict):
        shared_state = {"accessible": False, "pending_work": {"available": False, "blocked": True, "checkpoint": {"available": True, "verified": False, "reason": "status_unavailable"}}}
    progress_storage = shared_state.get("accessible") is True
    pending_work = shared_state.get("pending_work")
    if not isinstance(pending_work, dict):
        errors.append("pending_work")
        pending_work = {"available": False, "blocked": True, "checkpoint": {"available": True, "verified": False, "reason": "status_unavailable"}}
    ollama = _resolve_status_probe(
        ollama_future, "ollama", _status_ollama_fallback(model),
        timed_out, errors, unavailable,
    )
    if not isinstance(codex, dict):
        errors.append("codex")
        codex = {"installed": False, "version": None}
    if not isinstance(runner, dict):
        errors.append("runner")
        runner = {"running": False, "process_detected": False}
    if not isinstance(ollama, dict):
        errors.append("ollama")
        ollama = _status_ollama_fallback(model)
    progress_storage = progress_storage is True

    settings = github_settings
    interval_hours = settings.get("interval_hours", _DEFAULT_INTERVAL_HOURS)
    execution_mode = settings.get("execution_mode", "continuous")
    paused = bool(settings.get("paused", False))
    checks = {
        "ollama_online": ollama["online"],
        "model_installed": ollama["model_installed"],
        "model_selectable": ollama.get("model_selectable", ollama["model_installed"] and not _is_codex_context_variant(model)) is True,
        "model_is_codex_context_variant": ollama.get("model_is_codex_context_variant") is True,
        "model_context_metadata_available": ollama.get("model_metadata_available") is True,
        "model_context_supported": ollama.get("model_context_supported") is True,
        "model_tools_metadata_available": ollama.get("model_tools_metadata_available") is True,
        "model_supports_tools": ollama.get("model_supports_tools") is True,
        "codex_installed": codex["installed"],
        "runner_running": runner["running"],
        "github_cli_authenticated": github["authenticated"],
        "github_runs_available": github.get("runs_available", False),
        "progress_storage_accessible": progress_storage,
        "durable_state_available": pending_work.get("available") is True,
    }
    ready = all(
        value
        for key, value in checks.items()
        if key not in {"codex_installed", "model_is_codex_context_variant", "model_tools_metadata_available"}
    )
    active_runs = github.get("recent_runs", [])
    active = any(
        isinstance(item, dict)
        and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        for item in active_runs
    )
    can_dispatch = ready and not paused and not active
    return {
        "ready": ready,
        "can_dispatch": can_dispatch,
        "checks": checks,
        "pending_work": pending_work,
        "ollama": ollama,
        "codex": codex,
        "runner": runner,
        "github": github,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "model": model,
        "models": ollama.get("models", []),
        "selectable_models": ollama.get("selectable_models", ollama.get("models", [])),
        "model_is_codex_context_variant": ollama.get("model_is_codex_context_variant", False),
        "model_context_length": ollama.get("model_context_length"),
        "model_recommended_context_length": ollama.get("model_recommended_context_length"),
        "model_context_supported": ollama.get("model_context_supported", False),
        "model_context_error": ollama.get("model_context_error"),
        "model_capabilities": ollama.get("model_capabilities", []),
        "model_tools_metadata_available": ollama.get("model_tools_metadata_available", False),
        "model_tools_verification_method": ollama.get("model_tools_verification_method", "unverified"),
        "model_supports_tools": ollama.get("model_supports_tools", False),
        "model_metadata_error": ollama.get("model_metadata_error"),
        "interval_hours": interval_hours,
        "execution_mode": execution_mode,
        "paused": paused,
        "progress_storage_accessible": progress_storage,
        "platform": platform.platform(),
        "local_only_inference": True,
        "status_probe_timeouts": timed_out,
        "status_probe_errors": errors,
        "status_probe_unavailable": unavailable,
    }

@router.get("/status")
def autonomous_engineering_status() -> dict[str, Any]:
    return collect_status()


@router.post("/models/pull", status_code=202)
def autonomous_engineering_pull_model(payload: ModelPullRequest) -> dict[str, Any]:
    try:
        return start_model_pull(payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/models/pull/{job_id}")
def autonomous_engineering_model_pull_status(job_id: str) -> dict[str, Any]:
    state = get_model_pull(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="شناسهٔ عملیات دانلود مدل معتبر یا موجود نیست.")
    return state


def _enrich_progress_from_github(current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(current, dict):
        return current
    run_id = str(current.get("run_id", ""))
    if not run_id.isdigit():
        return current
    now = time.monotonic()
    if _RUN_PROGRESS_CACHE.get("run_id") == run_id and now - float(_RUN_PROGRESS_CACHE.get("at", 0)) < 12:
        cached = _RUN_PROGRESS_CACHE.get("value")
        return dict(cached) if isinstance(cached, dict) else current
    gh = shutil.which("gh")
    if not gh:
        return current
    result = _run(
        [gh, "api", f"repos/{_repository()}/actions/runs/{run_id}/jobs?per_page=100"],
        timeout=8,
    )
    if not result or result.returncode != 0:
        return current
    try:
        jobs = json.loads(result.stdout).get("jobs", [])
    except (ValueError, AttributeError):
        return current
    if not isinstance(jobs, list) or not jobs:
        return current
    updated = dict(current)
    active_job = next(
        (job for job in jobs if isinstance(job, dict) and job.get("status") == "in_progress"),
        None,
    )
    failed_job = next(
        (job for job in jobs if isinstance(job, dict) and job.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}),
        None,
    )
    queued_job = next(
        (job for job in jobs if isinstance(job, dict) and job.get("status") == "queued" and job.get("name") == "codex"),
        None,
    )
    if active_job:
        name = str(active_job.get("name", ""))
        steps = active_job.get("steps", [])
        steps = steps if isinstance(steps, list) else []
        active_step = next(
            (step for step in steps if isinstance(step, dict) and step.get("status") == "in_progress"),
            None,
        )
        step_name = str(active_step.get("name", "در حال اجرای مرحله")) if active_step else "در انتظار مرحلهٔ بعد"
        updated["activity"] = step_name[:180]
        updated["status"] = "running"
        if name == "codex":
            updated.setdefault("phase", "coding")
            updated.setdefault("phase_label", "اجرای چرخهٔ مهندسی")
    elif failed_job:
        updated.update({
            "status": "failed",
            "phase_label": "چرخه با خطا پایان یافت",
            "activity": str(failed_job.get("name", "مرحلهٔ ناموفق")) + ": " + str(failed_job.get("conclusion", "failed")),
        })
    elif queued_job and queued_job.get("name") == "codex":
        updated.update({
            "phase": "queued", "phase_label": "در انتظار Runner خودمیزبان",
            "activity": "چرخه در صف اجرا قرار دارد", "status": "queued",
        })
    elif all(
        isinstance(job, dict) and job.get("status") == "completed"
        for job in jobs
    ):
        conclusions = {job.get("conclusion") for job in jobs if isinstance(job, dict)}
        if conclusions.issubset({"success", "skipped", "neutral"}):
            updated.update({
                "status": "completed", "phase": "complete",
                "phase_label": "چرخهٔ مهندسی کامل شد",
                "activity": "تمام مراحل Workflow با موفقیت پایان یافتند",
                "percent": 100,
            })
    _RUN_PROGRESS_CACHE.update({"run_id": run_id, "at": now, "value": dict(updated)})
    return updated


@router.get("/progress")
def autonomous_engineering_progress(
    period: str = "daily", start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    result = get_progress(period=period, start_date=start_date, end_date=end_date)
    result["current"] = _enrich_progress_from_github(result.get("current"))
    return result


def _save_repository_variables(gh: str, updates: tuple[tuple[str, str], ...]) -> None:
    return _services.save_repository_variables(
        gh, updates, run_command=_run, repository=_repository()
    )


@router.post("/settings", status_code=200)
def save_autonomous_engineering_settings(payload: EngineeringSettings) -> dict[str, Any]:
    model = payload.model.strip()
    if not _MODEL_PATTERN.fullmatch(model):
        raise HTTPException(status_code=422, detail="شناسهٔ مدل Ollama معتبر نیست.")
    if _is_codex_context_variant(model):
        raise HTTPException(status_code=422, detail="این مدل نسخهٔ داخلی Context است؛ مدل اصلی Ollama را انتخاب کنید.")
    if payload.interval_hours not in _ALLOWED_INTERVAL_HOURS:
        raise HTTPException(status_code=422, detail="فاصلهٔ اجرا باید یکی از ۱، ۲، ۴، ۶، ۱۲ یا ۲۴ ساعت باشد.")
    if payload.execution_mode not in {"scheduled", "continuous"}:
        raise HTTPException(status_code=422, detail="حالت اجرا معتبر نیست.")
    local_model = _ollama_status(model)
    if not local_model.get("online"):
        raise HTTPException(status_code=503, detail="سرویس Ollama محلی در دسترس نیست؛ مدل ذخیره نشد.")
    if not local_model.get("model_installed"):
        raise HTTPException(status_code=422, detail="مدل انتخاب‌شده در فهرست مدل‌های نصب‌شدهٔ Ollama وجود ندارد.")
    if not local_model.get("model_metadata_available"):
        raise HTTPException(status_code=422, detail="حداکثر Context مدل از metadata معتبر Ollama قابل تأیید نیست؛ مدل ذخیره نشد.")
    if not local_model.get("model_context_supported"):
        raise HTTPException(
            status_code=422,
            detail="با توجه به metadata مدل و حافظهٔ آزاد فعلی، ظرفیت امن Context قابل محاسبه نیست؛ تنظیمات ذخیره نشد.",
        )
    if not local_model.get("model_supports_tools"):
        raise HTTPException(status_code=422, detail="مدل انتخاب‌شده قابلیت tools را اعلام نکرده و برای Codex قابل استفاده نیست.")
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    updates = (
        ("AUTONOMOUS_ENGINEERING_MODEL", model),
        ("AUTONOMOUS_ENGINEERING_INTERVAL_HOURS", str(payload.interval_hours)),
        ("AUTONOMOUS_ENGINEERING_MODE", payload.execution_mode),
    )
    _save_repository_variables(gh, updates)
    return {
        "saved": True,
        "model": model,
        "interval_hours": payload.interval_hours,
        "execution_mode": payload.execution_mode,
        "message": "تنظیمات عامل در متغیرهای مخزن GitHub ذخیره شد.",
    }


def _has_open_autonomous_pr(gh: str) -> bool:
    """Fail closed unless GitHub confirms there is no controller-owned open PR."""
    result = _run(
        [gh, "api", f"repos/{_repository()}/pulls?state=open&per_page=100"],
        timeout=12,
    )
    if not result or result.returncode != 0:
        raise HTTPException(
            status_code=503,
            detail="وضعیت PRهای باز مخزن قابل تأیید نیست؛ وظیفهٔ جدید ارسال نشد.",
        )
    try:
        pulls = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="پاسخ GitHub برای PRهای باز معتبر نیست؛ وظیفهٔ جدید ارسال نشد.",
        ) from exc
    if not isinstance(pulls, list) or any(not isinstance(item, dict) for item in pulls):
        raise HTTPException(
            status_code=502,
            detail="ساختار فهرست PRهای GitHub معتبر نیست؛ وظیفهٔ جدید ارسال نشد.",
        )
    for item in pulls:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
        branch = head.get("ref")
        if (
            user.get("login") == "github-actions[bot]"
            and head_repo.get("full_name") == _repository()
            and isinstance(branch, str)
            and branch.startswith("codex/local-engineering-")
            and base.get("ref") == "main"
            and item.get("state", "open") == "open"
        ):
            return True
    return False


@router.post("/dispatch", status_code=202)
def dispatch_autonomous_engineering(
    payload: ManualTaskRequest | None = None,
    request: Request = None,
) -> dict[str, Any]:
    global _LAST_DISPATCH_ACCEPTED_AT
    request_state = getattr(request, "state", None)
    correlation_id = getattr(request_state, "correlation_id", None)
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="درخواست اجرای دیگری در حال ثبت است؛ از ارسال هم‌زمان در چند زبانه خودداری کنید.",
            headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_IN_PROGRESS", "X-Retryable": "false"},
        )
    try:
        recent_acceptance_at = max(_LAST_DISPATCH_ACCEPTED_AT, _LAST_RESUME_ACCEPTED_AT)
        if (
            recent_acceptance_at
            and time.monotonic() - recent_acceptance_at < _DISPATCH_VISIBILITY_GRACE_SECONDS
        ):
            raise HTTPException(
                status_code=409,
                detail="درخواست اجرای قبلی تازه پذیرفته شده یا نتیجهٔ آن نامشخص است؛ پیش از ارسال دوباره، سوابق اجرا را بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_RECENTLY_ACCEPTED", "X-Retryable": "false"},
            )
        try:
            result = _dispatch_autonomous_engineering_locked(payload, correlation_id=correlation_id)
        except HTTPException as exc:
            exc.headers = dict(exc.headers or {})
            exc.headers.setdefault("X-Correlation-ID", correlation_id)
            if exc.headers.get("X-Error-Code") == "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN":
                _LAST_DISPATCH_ACCEPTED_AT = time.monotonic()
            raise
        if result.get("accepted") is True:
            _LAST_DISPATCH_ACCEPTED_AT = time.monotonic()
        return result
    finally:
        _DISPATCH_REQUEST_LOCK.release()


def _dispatch_autonomous_engineering_locked(
    payload: ManualTaskRequest | None = None,
    *,
    correlation_id: str | None = None,
    resume_once: bool = False,
) -> dict[str, Any]:
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex
    manual_task = payload.task.strip() if payload else ""
    if "\x00" in manual_task:
        raise HTTPException(status_code=422, detail="متن وظیفه شامل نویسهٔ نامعتبر است.")
    readiness = collect_status()
    if not readiness.get("github", {}).get("runs_available", False):
        raise HTTPException(status_code=503, detail="وضعیت اجراهای GitHub قابل دریافت نیست؛ اجرای جدید به‌صورت ایمن متوقف شد.")
    if readiness.get("paused", False) and not resume_once:
        raise HTTPException(status_code=409, detail="صف مهندسی در وضعیت توقف ایمن است؛ ابتدا ادامه را فعال کنید.")
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail="پیش‌نیازهای عامل کامل نیست؛ ابتدا وضعیت محلی را بررسی کنید.")
    pending = readiness.get("pending_work")
    if manual_task and not isinstance(pending, dict):
        # New work requires an explicit, current Broker snapshot. Empty dispatch
        # is a resume/reconcile request; the serialized controller makes the
        # authoritative recovery decision and must not be treated as new work.
        pending = pending_work_status()
    if manual_task:
        if not isinstance(pending, dict) or pending.get("available") is not True:
            raise HTTPException(
                status_code=503,
                detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ وظیفهٔ جدید ارسال نشد.",
            )
    elif isinstance(pending, dict) and pending.get("available") is not True:
        raise HTTPException(
            status_code=503,
            detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.",
        )
    elif not isinstance(pending, dict):
        checks = readiness.get("checks", {})
        if isinstance(checks, dict) and checks.get("durable_state_available") is False:
            raise HTTPException(
                status_code=503,
                detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.",
            )

    checkpoint = (
        pending.get("checkpoint")
        if isinstance(pending, dict) and isinstance(pending.get("checkpoint"), dict)
        else {}
    )
    if checkpoint.get("available") is True and checkpoint.get("verified") is not True:
        raise HTTPException(
            status_code=409,
            detail="checkpoint ذخیره‌شده وجود دارد اما یکپارچگی آن قابل تأیید نیست؛ اجرا متوقف شد تا تغییرات قبلی حفظ شوند.",
        )
    if manual_task and pending.get("blocked"):
        if checkpoint.get("available") is True:
            run_id = str(checkpoint.get("run_id") or "نامشخص")
            task = checkpoint.get("task", 1)
            detail = f"یک checkpoint تأییدشده برای اجرای {run_id} و وظیفهٔ {task} باقی مانده است. درخواست جدید پذیرفته نشد؛ اجرای بدون متن وظیفه همان کار را ادامه می‌دهد."
        else:
            detail = "یک تسک، PR یا عملیات پاک‌سازی قبلی هنوز تعیین‌تکلیف نشده است؛ متن وظیفهٔ جدید پذیرفته نشد."
        raise HTTPException(status_code=409, detail=detail)
    active_runs = readiness.get("github", {}).get("recent_runs", [])
    has_active_run = any(
        isinstance(item, dict)
        and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        for item in active_runs
    )
    if resume_once and has_active_run:
        return {
            "accepted": True,
            "repository": _repository(),
            "workflow": _WORKFLOW,
            "manual_task_submitted": False,
            "resume_once": True,
            "dispatch_status": "existing_run_active",
            "correlation_id": correlation_id,
            "message": "توقف برداشته شد؛ یک اجرای عامل از قبل فعال است، بنابراین برای جلوگیری از اجرای تکراری Workflow تازه‌ای ارسال نشد.",
        }
    if has_active_run:
        raise HTTPException(status_code=409, detail="یک اجرای عامل در حال انجام است؛ ابتدا وضعیت آن را بررسی کنید.")
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    if manual_task and _has_open_autonomous_pr(gh):
        raise HTTPException(
            status_code=409,
            detail="یک PR مهندسی خودکار هنوز باز است؛ ابتدا همان PR را بازبینی و تعیین‌تکلیف کنید.",
        )
    dispatch_command = [
        gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main",
        "--field", f"correlation_id={correlation_id}",
        "--field", f"manual_task={manual_task}",
    ]
    if resume_once:
        dispatch_command.extend(["--field", "resume_once=true"])
    try:
        result = _run(
            dispatch_command,
            timeout=20,
            raise_os_error=True,
        )
    except OSError:
        _STATUS_LOGGER.error(
            "Autonomous workflow dispatch could not start; correlation_id=%s",
            correlation_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "GitHub CLI اجرا نشد؛ نصب و دسترسی اجرای آن را بررسی کنید. پس از رفع مشکل، "
                "می‌توانید دوباره تلاش کنید. شناسهٔ پیگیری: " + correlation_id
            ),
            headers={
                "X-Correlation-ID": correlation_id,
                "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_LAUNCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_LAUNCH_FAILED",
                "X-Retryable": "true",
            },
        )
    if result is None:
        _STATUS_LOGGER.warning(
            "Autonomous workflow dispatch outcome is unknown; correlation_id=%s",
            correlation_id,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "نتیجهٔ ارسال Workflow نامشخص است؛ ممکن است GitHub درخواست را دریافت کرده باشد. "
                "پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: "
                + correlation_id
            ),
            headers={
                "X-Correlation-ID": correlation_id,
                "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN" if resume_once else "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN",
                "X-Retryable": "false",
            },
        )
    if result.returncode != 0:
        safe_stderr = _sanitize_workflow_log(str(result.stderr or ""))
        stderr_fingerprint = (
            hashlib.sha256(safe_stderr.encode("utf-8")).hexdigest()[:16]
            if safe_stderr else "empty"
        )
        _STATUS_LOGGER.error(
            "Autonomous workflow dispatch failed; correlation_id=%s exit_code=%s stderr_sha256=%s",
            correlation_id,
            result.returncode,
            stderr_fingerprint,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"ارسال Workflow ناموفق بود (exit code {result.returncode}). "
                "پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: "
                + correlation_id
            ),
            headers={
                "X-Correlation-ID": correlation_id,
                "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_FAILED",
                "X-Retryable": "false",
            },
        )
    return {
        "accepted": True,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "manual_task_submitted": bool(manual_task),
        "resume_once": bool(resume_once),
        "dispatch_status": "resume_once_dispatched" if resume_once else "dispatched",
        "correlation_id": correlation_id,
        "message": (
            "ادامه از وضعیت ذخیره‌شده درخواست شد؛ در حالت زمان‌بندی‌شده این اجرا یک‌باره است."
            if resume_once else
            "وظیفهٔ دستی ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید."
            if manual_task else
            "درخواست اجرای چرخهٔ خودکار ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید."
        ),
    }


@router.post("/pause")
def pause_autonomous_engineering() -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="دسترسی GitHub در حساب برنامه آماده نیست.")
    result = _run([gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "true", "--repo", _repository()], timeout=15)
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="ثبت توقف ایمن در GitHub ناموفق بود.")
    return {"accepted": True, "paused": True, "message": "توقف ایمن ثبت شد؛ وظیفهٔ جاری تا مرز امن ادامه می‌یابد و وظیفهٔ جدید آغاز نمی‌شود."}


@router.post("/resume")
def resume_autonomous_engineering(request: Request = None) -> dict[str, Any]:
    global _LAST_RESUME_ACCEPTED_AT
    request_state = getattr(request, "state", None)
    correlation_id = getattr(request_state, "correlation_id", None)
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex

    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="درخواست اجرای دیگری در حال ثبت است؛ ادامهٔ صف تا پایان همان عملیات متوقف می‌ماند.",
            headers={
                "X-Correlation-ID": correlation_id,
                "X-Error-Code": "AUTONOMOUS_RESUME_IN_PROGRESS",
                "X-Retryable": "false",
            },
        )

    try:
        if (
            _LAST_RESUME_ACCEPTED_AT
            and time.monotonic() - _LAST_RESUME_ACCEPTED_AT < _DISPATCH_VISIBILITY_GRACE_SECONDS
        ):
            return {
                "accepted": True,
                "paused": False,
                "dispatch_status": "recently_accepted",
                "correlation_id": correlation_id,
                "message": "درخواست ادامه به‌تازگی پذیرفته شده یا نتیجهٔ آن نامشخص است؛ برای جلوگیری از اجرای تکراری، dispatch تازه‌ای ارسال نشد. ابتدا تاریخچهٔ اجراها را بازخوانی کنید.",
            }

        gh = shutil.which("gh")
        if not gh:
            raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
        auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
        if not auth or auth.returncode != 0:
            raise HTTPException(status_code=503, detail="دسترسی GitHub در حساب برنامه آماده نیست.")

        result = _run(
            [gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "false", "--repo", _repository()],
            timeout=15,
        )
        if not result or result.returncode != 0:
            raise HTTPException(status_code=502, detail="ثبت ادامه در GitHub ناموفق بود.")

        try:
            dispatched = _dispatch_autonomous_engineering_locked(
                None,
                correlation_id=correlation_id,
                resume_once=True,
            )
        except HTTPException as exc:
            exc.headers = dict(exc.headers or {})
            exc.headers.setdefault("X-Correlation-ID", correlation_id)
            if exc.headers.get("X-Error-Code") == "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN":
                _LAST_RESUME_ACCEPTED_AT = time.monotonic()
            else:
                try:
                    rollback = _run(
                        [gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "true", "--repo", _repository()],
                        timeout=15,
                    )
                    if not rollback or rollback.returncode != 0:
                        _STATUS_LOGGER.error(
                            "Autonomous resume rollback could not restore Pause; correlation_id=%s",
                            correlation_id,
                        )
                except Exception:
                    _STATUS_LOGGER.exception(
                        "Autonomous resume rollback raised; correlation_id=%s",
                        correlation_id,
                    )
            raise
        except Exception as exc:
            _LAST_RESUME_ACCEPTED_AT = time.monotonic()
            try:
                rollback = _run(
                    [gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "true", "--repo", _repository()],
                    timeout=15,
                )
                if not rollback or rollback.returncode != 0:
                    _STATUS_LOGGER.error(
                        "Autonomous resume rollback after unexpected error failed; correlation_id=%s",
                        correlation_id,
                    )
            except Exception:
                _STATUS_LOGGER.exception(
                    "Autonomous resume rollback after unexpected error raised; correlation_id=%s",
                    correlation_id,
                )
            _STATUS_LOGGER.exception(
                "Autonomous resume outcome became ambiguous; correlation_id=%s",
                correlation_id,
            )
            raise HTTPException(
                status_code=502,
                detail="نتیجهٔ درخواست ادامه نامشخص شد؛ برای جلوگیری از اجرای تکراری، ابتدا تاریخچهٔ اجراها را بازخوانی کنید.",
                headers={
                    "X-Correlation-ID": correlation_id,
                    "X-Error-Code": "WORKFLOW_RESUME_OUTCOME_UNKNOWN",
                    "X-Retryable": "false",
                },
            ) from exc

        if dispatched.get("accepted") is True:
            _LAST_RESUME_ACCEPTED_AT = time.monotonic()
        return {**dispatched, "paused": False}
    finally:
        _DISPATCH_REQUEST_LOCK.release()


def _cancel_autonomous_engineering_locked() -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    result = _run(
        [
            gh, "run", "list", "--repo", _repository(), "--workflow", _WORKFLOW,
            "--limit", "30", "--json", "databaseId,status",
        ],
        timeout=12,
    )
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="وضعیت اجراهای GitHub قابل دریافت نیست.")
    try:
        runs = json.loads(result.stdout)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="پاسخ GitHub معتبر نیست.") from exc
    active_runs = [
        item for item in runs
        if isinstance(item, dict)
        and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        and isinstance(item.get("databaseId"), int)
        and not isinstance(item.get("databaseId"), bool)
    ]
    if not active_runs:
        raise HTTPException(status_code=409, detail="اجرای فعالی برای لغو وجود ندارد.")

    def _codex_job_state(run: dict[str, Any]) -> str | None:
        details = _run(
            [gh, "run", "view", str(run["databaseId"]), "--repo", _repository(), "--json", "jobs"],
            timeout=5,
        )
        if not details or details.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail="وضعیت jobهای اجرای فعال قابل تأیید نیست؛ برای جلوگیری از لغو اجرای اشتباه، عملیات متوقف شد.",
            )
        try:
            payload = json.loads(details.stdout)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="جزئیات jobهای اجرای فعال معتبر نیست.") from exc
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise HTTPException(status_code=502, detail="فهرست jobهای اجرای فعال در دسترس نیست.")
        codex_jobs = [
            job for job in jobs
            if isinstance(job, dict) and str(job.get("name") or "").strip().casefold() == "codex"
        ]
        if len(codex_jobs) > 1:
            raise HTTPException(status_code=502, detail="بیش از یک job با مالکیت Codex در یک اجرا گزارش شد؛ لغو ایمن ممکن نیست.")
        if not codex_jobs:
            return None
        return str(codex_jobs[0].get("status") or "").strip().casefold().replace("-", "_")

    in_progress_runs = [item for item in active_runs if item.get("status") == "in_progress"]
    if len(in_progress_runs) > 5:
        raise HTTPException(
            status_code=409,
            detail="تعداد اجراهای درحال‌بررسی بیش از حد است؛ برای جلوگیری از لغو اجرای اشتباه، عملیات لغو متوقف شد.",
        )

    active_codex_runs = []
    queued_codex_runs = []
    queued_job_states = {"queued", "waiting", "requested", "pending"}
    for run in in_progress_runs:
        job_state = _codex_job_state(run)
        if job_state == "in_progress":
            active_codex_runs.append(run)
        elif job_state in queued_job_states:
            queued_codex_runs.append(run)

    if len(active_codex_runs) > 1:
        raise HTTPException(
            status_code=409,
            detail="چند اجرای Codex هم‌زمان فعال گزارش شده‌اند؛ اجرای هدف برای لغو مبهم است و عملیات متوقف شد.",
        )
    if len(active_codex_runs) == 1:
        active = active_codex_runs[0]
        selection_reason = "codex_job_in_progress"
    elif len(queued_codex_runs) > 1:
        raise HTTPException(
            status_code=409,
            detail="چند اجرای Codex در صف گزارش شده‌اند؛ انتخاب اجرای هدف برای لغو مبهم است و عملیات متوقف شد.",
        )
    elif len(queued_codex_runs) == 1:
        active = queued_codex_runs[0]
        selection_reason = "codex_job_queued"
    elif len(active_runs) == 1:
        active = active_runs[0]
        selection_reason = "single_active_workflow"
    else:
        raise HTTPException(
            status_code=409,
            detail="چند Workflow فعال وجود دارد اما job فعال Codex یکتا نیست؛ برای جلوگیری از لغو اجرای اشتباه، عملیات متوقف شد.",
        )

    cancelled = _run(
        [gh, "run", "cancel", str(active["databaseId"]), "--repo", _repository()],
        timeout=12,
    )
    if not cancelled or cancelled.returncode != 0:
        raise HTTPException(status_code=502, detail="لغو اجرا توسط GitHub تأیید نشد.")
    return {
        "accepted": True,
        "run_id": active["databaseId"],
        "selection_reason": selection_reason,
        "message": "درخواست لغو اجرای انتخاب‌شده به GitHub ارسال شد.",
    }


@router.post("/cancel")
def cancel_autonomous_engineering() -> dict[str, Any]:
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="ثبت یا ادامهٔ اجرای دیگری در حال انجام است؛ پس از پایان آن دوباره لغو را امتحان کنید.",
            headers={
                "X-Error-Code": "AUTONOMOUS_CANCEL_IN_PROGRESS",
                "X-Retryable": "false",
            },
        )
    try:
        return _cancel_autonomous_engineering_locked()
    finally:
        _DISPATCH_REQUEST_LOCK.release()


# Operational administration -------------------------------------------------
_ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "requested", "pending"}
_PROGRESS_CLEANUP_FILES = (
    "events.jsonl",
    "current.json",
    "events.jsonl.tmp",
    "current.json.tmp",
    "history.jsonl.tmp",
    "scorecard-state.json.tmp",
    "scorecard-baseline.json.tmp",
)
_MAX_DASHBOARD_LOG_CHARS = 120_000
_MAX_RUN_DIAGNOSTIC_BYTES = 240_000
_MAX_RUN_DIAGNOSTIC_ENTRIES = 8
_RUNTIME_CLEANUP_FILES = (
    ".agent-request.json",
    ".agent-github-state.json",
    ".agent-progress.json",
)


def _agent_runtime_directory():
    return Path(__file__).resolve().parents[1]


def _authenticated_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    return gh


def _github_api_json(gh: str, path: str, *, timeout: int = 15) -> dict[str, Any]:
    result = _run([gh, "api", f"repos/{_repository()}/{path}"], timeout=timeout)
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="دریافت اطلاعات اجرا از GitHub ناموفق بود.")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="پاسخ GitHub برای اطلاعات اجرا معتبر نیست.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="ساختار پاسخ GitHub برای اطلاعات اجرا معتبر نیست.")
    return value


def _workflow_runs(gh: str) -> list[dict[str, Any]]:
    # Keep the CLI/API command shape fixed. HTTP query parameters must never
    # become command-line arguments through the GitHub CLI wrapper.
    data = _github_api_json(
        gh,
        f"actions/workflows/{_WORKFLOW}/runs?per_page=100",
        timeout=20,
    )
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise HTTPException(status_code=502, detail="فهرست اجراهای Workflow در پاسخ GitHub وجود ندارد.")
    normalized: list[dict[str, Any]] = []
    for item in runs:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        diagnostic = _read_run_diagnostics(item["id"])
        latest_diagnostic = diagnostic["diagnostics"][-1] if diagnostic else None
        normalized.append({
            "id": item["id"],
            "status": str(item.get("status") or "unknown"),
            "conclusion": item.get("conclusion"),
            "name": str(item.get("name") or "Autonomous local Ollama engineering"),
            "display_title": str(item.get("display_title") or item.get("displayTitle") or ""),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "url": item.get("html_url") if isinstance(item.get("html_url"), str) else None,
            "head_branch": item.get("head_branch"),
            "workflow_path": str(item.get("path") or "").split("@", 1)[0],
            "failure_summary": (
                latest_diagnostic.get("summary")
                if latest_diagnostic and item.get("conclusion") not in {"success", "neutral", "skipped"}
                else None
            ),
        })
    return normalized


def _verified_workflow_run(gh: str, run_id: int) -> dict[str, Any]:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise HTTPException(status_code=422, detail="شناسهٔ اجرای GitHub معتبر نیست.")
    expected_path = f".github/workflows/{_WORKFLOW}"
    # Resolve the user-selected ID against the server-fetched, workflow-scoped
    # run list. Only the canonical ID returned by GitHub is used by CLI calls.
    for run in _workflow_runs(gh):
        if run.get("id") == run_id and run.get("workflow_path") == expected_path:
            return run
    raise HTTPException(status_code=404, detail="اجرای درخواستی متعلق به عامل مهندسی خودکار نیست.")


def _read_run_diagnostics(run_id: int) -> dict[str, Any] | None:
    """Read the runner-persisted, redacted report for one verified run ID."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    try:
        payload = read_run_diagnostics(run_id)
        if not isinstance(payload, dict):
            return None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or str(payload.get("run_id")) != str(run_id)
        or not isinstance(payload.get("diagnostics"), list)
    ):
        return None
    entries = []
    for item in payload["diagnostics"][-_MAX_RUN_DIAGNOSTIC_ENTRIES:]:
        if not isinstance(item, dict):
            continue
        entries.append({
            "run_attempt": str(item.get("run_attempt") or "")[:12],
            "task": item.get("task") if isinstance(item.get("task"), int) else None,
            "attempt": item.get("attempt") if isinstance(item.get("attempt"), int) else None,
            "stage": _sanitize_workflow_log(str(item.get("stage") or ""))[:80],
            "category": _sanitize_workflow_log(str(item.get("category") or ""))[:80],
            "summary": _sanitize_workflow_log(str(item.get("summary") or ""))[:800],
            "detail": _sanitize_workflow_log(str(item.get("detail") or ""))[-12_000:],
            "recorded_at": str(item.get("recorded_at") or "")[:64],
        })
    if not entries:
        return None
    return {
        "run_id": run_id,
        "updated_at": str(payload.get("updated_at") or "")[:64],
        "diagnostics": entries,
    }


def _workflow_failure_summary(value: str, failures: list[dict[str, Any]]) -> str | None:
    """Prefer the controller's root-cause line over generic shell exit messages."""
    lines = [line.strip() for line in _sanitize_workflow_log(value).splitlines() if line.strip()]
    if any("Warning: no last agent message" in line for line in lines):
        return (
            "Codex did not emit a final agent message; the output-last-message report was empty, "
            "so the controller could not validate a completion marker. Preserve the durable "
            "task/checkpoint and inspect the Codex turn's terminal events before retrying."
        )
    preferred = []
    for line in lines:
        marker = "Autonomous engineering cycle failed:"
        if marker in line:
            preferred.append(line.split(marker, 1)[1].strip())
        elif "Codex reported task " in line and " as blocked" in line:
            preferred.append(line)
    if preferred:
        return preferred[-1][:800]
    actionable = [
        line for line in lines
        if (
            ("##[error]" in line or re.search(r"(?i)\b(error|exception|traceback):", line))
            and "Process completed with exit code" not in line
        )
    ]
    if actionable:
        return actionable[-1][:800]
    if failures:
        first = failures[0]
        failed_steps = first.get("failed_steps") if isinstance(first.get("failed_steps"), list) else []
        step = failed_steps[0].get("name") if failed_steps and isinstance(failed_steps[0], dict) else None
        return (f"{first.get('name', 'Job')} failed" + (f" at {step}" if step else ""))[:800]
    return None


def _sanitize_workflow_log(value: str) -> str:
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)\S+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"""(?i)\b[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|gh[_-]?token)[\w-]*(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s]+)""",
        r"[REDACTED]\1[REDACTED]",
        value,
    )
    if len(value) > _MAX_DASHBOARD_LOG_CHARS:
        value = "[ابتدای گزارش به‌دلیل محدودیت حجم حذف شده است.]\n" + value[-_MAX_DASHBOARD_LOG_CHARS:]
    return value


@router.get("/runs")
def autonomous_engineering_runs() -> dict[str, Any]:
    gh = _authenticated_gh()
    return {"runs": _workflow_runs(gh), "repository": _repository(), "workflow": _WORKFLOW}


@router.get("/runs/{run_id}/logs")
def autonomous_engineering_run_logs(run_id: int) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, run_id)
    trusted_run_id = run["id"]
    jobs_data = _github_api_json(gh, f"actions/runs/{trusted_run_id}/jobs?per_page=100", timeout=20)
    jobs = jobs_data.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    failures = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        failed_steps = [
            {"name": str(step.get("name") or ""), "conclusion": str(step.get("conclusion") or "")}
            for step in steps
            if isinstance(step, dict) and step.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}
        ]
        if job.get("conclusion") not in {"success", "skipped", "neutral"} or failed_steps:
            failures.append({
                "name": str(job.get("name") or "Job"),
                "status": str(job.get("status") or "unknown"),
                "conclusion": job.get("conclusion"),
                "failed_steps": failed_steps,
            })
    logs_result = _run(
        [gh, "run", "view", str(trusted_run_id), "--log", "--repo", _repository()],
        timeout=45,
    )
    raw_log = ""
    if logs_result and logs_result.returncode == 0:
        raw_log = logs_result.stdout or ""
    elif logs_result:
        raw_log = logs_result.stdout or logs_result.stderr or ""
    if not raw_log and run.get("status") != "completed":
        raw_log = "گزارش کامل تا پایان اجرای Workflow در دسترس نیست."
    elif not raw_log and run.get("status") == "completed":
        raw_log = "GitHub گزارشی برای این اجرا برنگرداند؛ جزئیات مرحله‌های ناموفق در بالا آمده است."
    diagnostic = _read_run_diagnostics(trusted_run_id)
    diagnostic_entries = diagnostic.get("diagnostics", []) if diagnostic else []
    latest_diagnostic = diagnostic_entries[-1] if diagnostic_entries else None
    error_summary = (
        latest_diagnostic.get("summary")
        if latest_diagnostic else _workflow_failure_summary(raw_log, failures)
    )
    return {
        "run": {
            "id": trusted_run_id,
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "url": run.get("url"),
        },
        "failures": failures,
        "error_summary": error_summary,
        "failure_stage": latest_diagnostic.get("stage") if latest_diagnostic else (
            failures[0].get("name") if failures else None
        ),
        "diagnostic": diagnostic,
        "diagnostics_available": bool(diagnostic_entries),
        "logs": _sanitize_workflow_log(raw_log),
        "truncated": len(raw_log) > _MAX_DASHBOARD_LOG_CHARS,
    }


@router.delete("/runs/{run_id}")
def delete_autonomous_engineering_run(run_id: int) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, run_id)
    trusted_run_id = run["id"]
    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="فقط اجرای پایان‌یافته قابل حذف است؛ اجرای فعال حفظ شد.")
    result = _run(
        [gh, "api", "--method", "DELETE", f"repos/{_repository()}/actions/runs/{trusted_run_id}"],
        timeout=20,
    )
    if not result or result.returncode != 0:
        _STATUS_LOGGER.warning(
            "GitHub run deletion failed; run_id=%s returncode=%s stderr_present=%s",
            trusted_run_id,
            result.returncode if result else None,
            bool(result and result.stderr),
        )
        raise HTTPException(
            status_code=502,
            detail="حذف اجرای GitHub تأیید نشد؛ وضعیت سرویس را بازخوانی کنید.",
        )
    return {"deleted": True, "run_id": trusted_run_id, "message": "اجرای پایان‌یافته از تاریخچهٔ GitHub حذف شد."}


def _cleanup_autonomous_engineering_temporary_data_locked() -> dict[str, Any]:
    removed = 0
    removed_bytes = 0
    failed_files: list[str] = []
    if _is_windows():
        try:
            result = cleanup_shared_progress_data()
            removed = int(result.get("files_removed", 0))
            removed_bytes = int(result.get("bytes_removed", 0))
            failed_files.extend(str(item) for item in result.get("failed_files", []))
        except RuntimeError as exc:
            from .autonomous_engineering_state_broker import StateBrokerConflict
            if isinstance(exc, StateBrokerConflict):
                blockers = ", ".join(exc.blockers)
                raise HTTPException(
                    status_code=409,
                    detail="وضعیت پایدار عامل هنوز تعیین‌تکلیف نشده است؛ پاک‌سازی انجام نشد: " + blockers,
                ) from exc
            raise HTTPException(
                status_code=503,
                detail=f"پاک‌سازی امن فضای مشترک ممکن نشد؛ وضعیت پایدار حفظ شد ({type(exc).__name__}).",
            ) from exc
    else:
        directory = execution_state_directory()
        marker_names = (
            "pending-manual-task.txt",
            "pending-manual-task-owner.json",
            "pending-autonomous-pr.json",
            "pending-state-cleanup.json",
        )
        try:
            if directory.is_dir():
                with durable_state_lock(directory, timeout_seconds=2):
                    with checkpoint_store_lock(directory, timeout_seconds=2):
                        blockers = [name for name in marker_names if (directory / name).exists()]
                        if blockers:
                            raise HTTPException(
                                status_code=409,
                                detail="وضعیت پایدار عامل هنوز تعیین‌تکلیف نشده است؛ پاک‌سازی انجام نشد: " + ", ".join(blockers),
                            )
                        for name in _PROGRESS_CLEANUP_FILES:
                            path = directory / name
                            try:
                                if not path.is_file() or path.is_symlink():
                                    continue
                                size = path.stat().st_size
                                path.unlink()
                                removed += 1
                                removed_bytes += size
                            except FileNotFoundError:
                                continue
                            except OSError:
                                failed_files.append(str(path))
        except HTTPException:
            raise
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"پاک‌سازی امن فضای موقت ممکن نشد؛ checkpointها و وضعیت پایدار عامل حفظ شدند ({type(exc).__name__}).",
            ) from exc

    # On Windows the LocalService broker owns the shared repository lock and
    # cannot safely hand that lock back to this process. It cleans the protected
    # state files itself; worktree runtime files are preserved rather than
    # deleted after the broker releases its lock. POSIX cleanup runs under the
    # caller's repository lock and may remove these files safely.
    runtime_files_preserved: list[str] = []
    if _is_windows():
        runtime_files_preserved = list(_RUNTIME_CLEANUP_FILES)
    else:
        runtime_directory = _agent_runtime_directory()
        for name in _RUNTIME_CLEANUP_FILES:
            path = runtime_directory / name
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                size = path.stat().st_size
                path.unlink()
                removed += 1
                removed_bytes += size
            except FileNotFoundError:
                continue
            except OSError:
                failed_files.append(str(path))

    preserved = [
        "history.jsonl",
        "scorecard-baseline.json",
        "checkpoint files",
        "pending task/PR markers",
        *runtime_files_preserved,
    ]
    return {
        "cleaned": not failed_files,
        "files_removed": removed,
        "bytes_removed": removed_bytes,
        "failed_files": failed_files,
        "preserved": preserved,
        "message": (
            "پاک‌سازی داده‌های امن انجام شد؛ تاریخچه، checkpointها و وضعیت پایدار عامل حفظ شدند."
            + (" فایل‌های runtime داخل worktree در Windows برای جلوگیری از race حفظ شدند." if runtime_files_preserved else "")
            if not failed_files
            else "پاک‌سازی ناقص بود؛ بعضی فایل‌های موقت به‌دلیل خطای دسترسی حذف نشدند."
        ),
    }

@router.post("/cleanup")
def cleanup_autonomous_engineering_temporary_data() -> dict[str, Any]:
    gh = _authenticated_gh()

    def ensure_no_active_workflow() -> None:
        runs = _workflow_runs(gh)
        if any(item.get("status") in _ACTIVE_RUN_STATES for item in runs):
            raise HTTPException(
                status_code=409,
                detail="عامل هنوز اجرای فعال یا در صف دارد؛ پاک‌سازی داده‌های موقت تا پایان اجرا مجاز نیست.",
            )

    ensure_no_active_workflow()
    try:
        if _is_windows():
            # The Windows broker owns the repository -> durable-state ->
            # checkpoint-store lock order itself. Do not acquire the same
            # repository lock in this client process or the broker would deadlock.
            return _cleanup_autonomous_engineering_temporary_data_locked()
        # POSIX uses the same repository lock as the Runner, then the helper
        # acquires durable-state and checkpoint-store locks in that order.
        with repository_execution_lock("ui-cleanup", "1"):
            ensure_no_active_workflow()
            return _cleanup_autonomous_engineering_temporary_data_locked()
    except RuntimeError as exc:
        if "Another autonomous execution owns this repository worktree" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="اجرای دیگری مالک worktree است؛ پاک‌سازی تا آزادشدن قفل انجام نمی‌شود.",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=f"قفل یا پاک‌سازی امن فضای عامل در دسترس نیست ({type(exc).__name__}).",
        ) from exc
