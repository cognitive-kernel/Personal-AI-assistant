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
from .autonomous_engineering_contracts import (
    CancelRequest,
    CODEX_CONTEXT_VARIANT_PATTERN as _CODEX_CONTEXT_VARIANT_PATTERN,
    CORRELATION_ID_PATTERN as _CORRELATION_ID_PATTERN,
    EngineeringSettings,
    HTTP_ERROR_CODES as _HTTP_ERROR_CODES,
    ManualTaskRequest,
    MODEL_PATTERN as _MODEL_PATTERN,
    ModelPullRequest,
    RETRYABLE_HTTP_STATUSES as _RETRYABLE_HTTP_STATUSES,
)

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
from .autonomous_engineering_dispatch_lock import DispatchAdmissionError, dispatch_admission_lock
from .autonomous_engineering_state_broker import StateBrokerConflict, StateBrokerError
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

# Fast local contention guard; the protected State Broker owns the
# cross-process, restart-durable dispatch reservation and grace window.
_DISPATCH_REQUEST_LOCK = threading.Lock()



from .autonomous_engineering_errors import (
    AutonomousEngineeringAPIRoute,
    _structured_error_response,
)
from . import autonomous_engineering_github as _github_adapter
from . import autonomous_engineering_run_log_safety as _run_log_safety
from . import autonomous_engineering_run_history as _run_history
from . import autonomous_engineering_settings as _settings_backend
from . import autonomous_engineering_readiness as _readiness_backend
from . import autonomous_engineering_run_logs as _run_logs_backend
from . import autonomous_engineering_pr_preflight as _pr_preflight
from . import autonomous_engineering_progress_projection as _progress_projection

# Backward-compatible cache seam used by existing tests and integrations.
_RUN_DIAGNOSTICS_CACHE = _run_history._DIAGNOSTICS_CACHE


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
def _is_codex_context_variant(model: str) -> bool:
    return bool(_CODEX_CONTEXT_VARIANT_PATTERN.search(model.strip()))


def _repository() -> str:
    value = os.getenv("AUTONOMOUS_ENGINEERING_REPOSITORY", _DEFAULT_REPOSITORY).strip()
    if "/" not in value or any(part in value for part in (" ", "\\", "..")):
        return _DEFAULT_REPOSITORY
    owner, name = value.split("/", 1)
    return value if owner and name and "/" not in name else _DEFAULT_REPOSITORY


def _run(
    command: list[str], timeout: int = 8, *, raise_os_error: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            input=input_text,
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

    return _readiness_backend.build_status_payload(
        github=github,
        github_settings=github_settings,
        codex=codex,
        runner=runner,
        ollama=ollama,
        pending_work=pending_work,
        progress_storage=progress_storage,
        model=model,
        repository=_repository(),
        workflow=_WORKFLOW,
        platform_name=platform.platform(),
        timed_out=timed_out,
        errors=errors,
        unavailable=unavailable,
        default_interval_hours=_DEFAULT_INTERVAL_HOURS,
        is_codex_context_variant=_is_codex_context_variant,
    )

@router.get("/status")
def autonomous_engineering_status() -> dict[str, Any]:
    return collect_status()


@router.post("/broker/setup")
def autonomous_engineering_setup_broker() -> dict[str, Any]:
    """Provision the durable-state broker only after an explicit UI/operator action."""
    from .autonomous_engineering_broker_setup import BrokerSetupError, setup_state_broker

    try:
        return setup_state_broker()
    except BrokerSetupError as exc:
        error = HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers={
                "X-Error-Code": exc.code,
                "X-Retryable": "true" if exc.retryable else "false",
            },
        )
        error.safe_details = exc.safe_details
        raise error from exc


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
    updated = _progress_projection.enrich_progress_payload(current, jobs)
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
    return _settings_backend.save_settings(
        payload,
        model_pattern=_MODEL_PATTERN,
        allowed_interval_hours=_ALLOWED_INTERVAL_HOURS,
        is_codex_context_variant=_is_codex_context_variant,
        ollama_status=_ollama_status,
        which=shutil.which,
        run_command=_run,
        save_repository_variables=_save_repository_variables,
    )

def _has_open_autonomous_pr(gh: str) -> bool:
    return _pr_preflight.has_open_autonomous_pr(
        gh, repository=_repository(), run_command=_run
    )

def _dispatch_with_admission(payload: ManualTaskRequest | None, *, correlation_id: str, resume_once: bool = False) -> dict[str, Any]:
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="درخواست اجرای دیگری در حال ثبت است؛ از ارسال هم‌زمان در چند زبانه خودداری کنید.",
            headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_IN_PROGRESS", "X-Retryable": "false"},
        )
    try:
        try:
            with dispatch_admission_lock() as admission:
                try:
                    result = _dispatch_autonomous_engineering_locked(
                        payload,
                        correlation_id=correlation_id,
                        resume_once=resume_once,
                    )
                except HTTPException as exc:
                    error_code = (exc.headers or {}).get("X-Error-Code")
                    if error_code in {
                        "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN",
                        "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN",
                    }:
                        admission["accepted"] = True
                    raise
                if result.get("accepted") is True:
                    admission["accepted"] = True
                return result
        except StateBrokerConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="درخواست اجرای دیگری در حال ثبت است یا پنجرهٔ جلوگیری از اجرای تکراری هنوز فعال است؛ وضعیت را بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_RECENTLY_ACCEPTED", "X-Retryable": "false"},
            ) from exc
        except (StateBrokerError, DispatchAdmissionError) as exc:
            _STATUS_LOGGER.error("Persistent dispatch admission unavailable; correlation_id=%s", correlation_id)
            raise HTTPException(
                status_code=503,
                detail="قفل پایدار کنترل ارسال در دسترس یا معتبر نیست؛ برای جلوگیری از اجرای تکراری، درخواست متوقف شد.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_ADMISSION_UNAVAILABLE", "X-Retryable": "false"},
            ) from exc
        except RuntimeError as exc:
            _STATUS_LOGGER.error("Autonomous dispatch preflight failed closed; correlation_id=%s", correlation_id)
            raise HTTPException(
                status_code=503,
                detail="پیش‌بررسی اجرای عامل قابل تأیید نیست؛ هیچ Workflow جدیدی ارسال نشد.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_PREFLIGHT_FAILED", "X-Retryable": "false"},
            ) from exc
    finally:
        _DISPATCH_REQUEST_LOCK.release()


@router.post("/dispatch", status_code=202)
def dispatch_autonomous_engineering(payload: ManualTaskRequest | None = None, request: Request = None) -> dict[str, Any]:
    request_state = getattr(request, "state", None)
    correlation_id = getattr(request_state, "correlation_id", None)
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex
    return _dispatch_with_admission(payload, correlation_id=correlation_id)


def _set_autonomous_engineering_pause(gh: str, paused: bool) -> bool:
    try:
        result = _run(
            [
                gh,
                "variable",
                "set",
                "AUTONOMOUS_ENGINEERING_PAUSED",
                "--body",
                "true" if paused else "false",
                "--repo",
                _repository(),
            ],
            timeout=15,
        )
    except OSError:
        return False
    return bool(result and result.returncode == 0)


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
        pending = pending_work_status()
    if manual_task and (not isinstance(pending, dict) or pending.get("available") is not True):
        raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ وظیفهٔ جدید ارسال نشد.")
    if not manual_task and isinstance(pending, dict) and pending.get("available") is not True:
        raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.")
    if not isinstance(pending, dict):
        checks = readiness.get("checks", {})
        if isinstance(checks, dict) and checks.get("durable_state_available") is False:
            raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.")
    checkpoint = pending.get("checkpoint") if isinstance(pending, dict) and isinstance(pending.get("checkpoint"), dict) else {}
    if checkpoint.get("available") is True and checkpoint.get("verified") is not True:
        raise HTTPException(status_code=409, detail="checkpoint ذخیره‌شده وجود دارد اما یکپارچگی آن قابل تأیید نیست؛ اجرا متوقف شد تا تغییرات قبلی حفظ شوند.")
    if manual_task and pending.get("blocked"):
        if checkpoint.get("available") is True:
            detail = f"یک checkpoint تأییدشده برای اجرای {checkpoint.get('run_id') or 'نامشخص'} و وظیفهٔ {checkpoint.get('task', 1)} باقی مانده است. درخواست جدید پذیرفته نشد؛ اجرای بدون متن وظیفه همان کار را ادامه می‌دهد."
        else:
            detail = "یک تسک، PR یا عملیات پاک‌سازی قبلی هنوز تعیین‌تکلیف نشده است؛ متن وظیفهٔ جدید پذیرفته نشد."
        raise HTTPException(status_code=409, detail=detail)
    active_runs = readiness.get("github", {}).get("recent_runs", [])
    has_active_run = any(
        isinstance(item, dict)
        and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        for item in active_runs
    )
    if resume_once and not readiness.get("paused", False) and not has_active_run:
        raise HTTPException(
            status_code=409,
            detail="صف مهندسی در وضعیت توقف نیست و اجرای فعالی برای ادامه وجود ندارد؛ درخواست Resume تکراری ارسال نشد.",
            headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_NOT_PAUSED", "X-Retryable": "false"},
        )
    if resume_once and has_active_run:
        gh = _authenticated_gh()
        if not _set_autonomous_engineering_pause(gh, False):
            restored = _set_autonomous_engineering_pause(gh, True)
            raise HTTPException(
                status_code=502,
                detail=(
                    "برداشتن توقف برای اجرای فعال تأیید نشد؛ وضعیت توقف دوباره ثبت شد."
                    if restored
                    else "برداشتن توقف و بازگردانی آن در GitHub هر دو نامشخص‌اند؛ وضعیت را فوراً بازخوانی کنید."
                ),
                headers={
                    "X-Correlation-ID": correlation_id,
                    "X-Error-Code": "WORKFLOW_RESUME_STATE_UPDATE_FAILED" if restored else "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED",
                    "X-Retryable": "false",
                },
            )
        return {
            "accepted": True,
            "repository": _repository(),
            "workflow": _WORKFLOW,
            "manual_task_submitted": False,
            "resume_once": True,
            "dispatch_status": "existing_run_active",
            "paused": False,
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
        raise HTTPException(status_code=409, detail="یک PR مهندسی خودکار هنوز باز است؛ ابتدا همان PR را بازبینی و تعیین‌تکلیف کنید.")
    command = [gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main"]
    workflow_input = None
    if manual_task:
        # Keep owner-provided task text out of argv; gh reads workflow inputs from stdin.
        command.append("--json")
        inputs = {"correlation_id": correlation_id, "manual_task": manual_task}
        if resume_once:
            inputs["resume_once"] = True
        workflow_input = json.dumps(inputs, ensure_ascii=False)
    else:
        command.extend(["--raw-field", f"correlation_id={correlation_id}", "--raw-field", "manual_task="])
        if resume_once:
            command.extend(["--raw-field", "resume_once=true"])
            if not _set_autonomous_engineering_pause(gh, False):
                restored = _set_autonomous_engineering_pause(gh, True)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "برداشتن توقف تأیید نشد؛ Workflow ارسال نشد و توقف دوباره ثبت شد."
                        if restored
                        else "برداشتن توقف تأیید نشد و بازگردانی آن نیز نامشخص است؛ Workflow ارسال نشد، وضعیت را فوراً بازخوانی کنید."
                    ),
                    headers={
                        "X-Correlation-ID": correlation_id,
                        "X-Error-Code": "WORKFLOW_RESUME_STATE_UPDATE_FAILED" if restored else "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED",
                        "X-Retryable": "false",
                    },
                )
    try:
        if workflow_input is not None:
            result = _run(command, timeout=20, raise_os_error=True, input_text=workflow_input)
        else:
            result = _run(command, timeout=20, raise_os_error=True)
    except OSError as exc:
        if resume_once and not _set_autonomous_engineering_pause(gh, True):
            raise HTTPException(
                status_code=502,
                detail="ارسال Workflow آغاز نشد و بازگردانی توقف در GitHub نیز تأیید نشد؛ وضعیت را فوراً بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED", "X-Retryable": "false"},
            ) from exc
        _STATUS_LOGGER.error("Autonomous workflow dispatch could not start; correlation_id=%s", correlation_id)
        raise HTTPException(status_code=503, detail="GitHub CLI اجرا نشد؛ درخواست به GitHub ارسال نشده است و می‌توانید پس از بررسی نصب و دسترسی، دوباره تلاش کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_LAUNCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_LAUNCH_FAILED", "X-Retryable": "true"}) from exc
    if result is None:
        _STATUS_LOGGER.warning("Autonomous workflow dispatch outcome is unknown; correlation_id=%s", correlation_id)
        raise HTTPException(status_code=502, detail="نتیجهٔ ارسال Workflow نامشخص است؛ ممکن است GitHub درخواست را دریافت کرده باشد. پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN" if resume_once else "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN", "X-Retryable": "false"})
    if result.returncode != 0:
        if resume_once and not _set_autonomous_engineering_pause(gh, True):
            raise HTTPException(
                status_code=502,
                detail="GitHub ارسال Workflow را رد کرد اما بازگردانی توقف نیز تأیید نشد؛ وضعیت را فوراً بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED", "X-Retryable": "false"},
            )
        safe_stderr = _sanitize_workflow_log(str(result.stderr or ""))
        stderr_fingerprint = hashlib.sha256(safe_stderr.encode("utf-8")).hexdigest()[:16] if safe_stderr else "empty"
        _STATUS_LOGGER.error("Autonomous workflow dispatch failed; correlation_id=%s exit_code=%s stderr_sha256=%s", correlation_id, result.returncode, stderr_fingerprint)
        raise HTTPException(status_code=502, detail=f"ارسال Workflow ناموفق بود (exit code {result.returncode}). پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_FAILED", "X-Retryable": "false"})
    return {
        "accepted": True,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "manual_task_submitted": bool(manual_task),
        "resume_once": bool(resume_once),
        "dispatch_status": "resume_once_dispatched" if resume_once else "dispatched",
        "paused": False if resume_once else readiness.get("paused", False),
        "correlation_id": correlation_id,
        "message": "ادامه از وضعیت ذخیره‌شده درخواست شد؛ در حالت زمان‌بندی‌شده این اجرا یک‌باره است." if resume_once
            else "وظیفهٔ دستی ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید." if manual_task
            else "درخواست اجرای چرخهٔ خودکار ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید.",
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
    request_state = getattr(request, "state", None)
    correlation_id = getattr(request_state, "correlation_id", None)
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex
    result = _dispatch_with_admission(None, correlation_id=correlation_id, resume_once=True)
    result["message"] = (
        "توقف برداشته شد و اجرای فعال ادامه می‌یابد؛ Workflow تکراری ارسال نشد."
        if result.get("dispatch_status") == "existing_run_active"
        else "ادامه از وضعیت ذخیره‌شده درخواست شد؛ در حالت زمان‌بندی‌شده این اجرا یک‌باره است."
    )
    return result


@router.post("/cancel")
def cancel_autonomous_engineering(payload: CancelRequest) -> dict[str, Any]:
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="ثبت، ادامه یا لغو اجرای دیگری در حال انجام است؛ پس از پایان آن دوباره تلاش کنید.", headers={"X-Error-Code": "AUTONOMOUS_CANCEL_IN_PROGRESS", "X-Retryable": "false"})
    try:
        return _cancel_autonomous_engineering_locked(payload)
    finally:
        _DISPATCH_REQUEST_LOCK.release()


def _cancel_autonomous_engineering_locked(payload: CancelRequest) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, payload.run_id)
    trusted_run_id = run["id"]
    status = run.get("status")
    if status not in _ACTIVE_RUN_STATES:
        raise HTTPException(status_code=409, detail="اجرای انتخاب‌شده دیگر فعال نیست؛ وضعیت را بازخوانی کنید.")
    if status == "in_progress":
        details = _run(
            [gh, "run", "view", str(trusted_run_id), "--repo", _repository(), "--json", "jobs"],
            timeout=5,
        )
        if not details or details.returncode != 0:
            raise HTTPException(status_code=502, detail="وضعیت jobهای اجرای انتخاب‌شده قابل تأیید نیست؛ لغو ایمن متوقف شد.")
        try:
            jobs_payload = json.loads(details.stdout)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="جزئیات jobهای اجرای انتخاب‌شده معتبر نیست.") from exc
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
        if not isinstance(jobs, list):
            raise HTTPException(status_code=502, detail="فهرست jobهای اجرای انتخاب‌شده در دسترس نیست.")
        codex_jobs = [
            job for job in jobs
            if isinstance(job, dict) and str(job.get("name") or "").strip().casefold() == "codex"
        ]
        if len(codex_jobs) > 1:
            raise HTTPException(status_code=502, detail="بیش از یک job با مالکیت Codex در اجرای انتخاب‌شده گزارش شد؛ لغو ایمن ممکن نیست.")
        if not codex_jobs or str(codex_jobs[0].get("status") or "").strip().casefold().replace("-", "_") not in _ACTIVE_RUN_STATES:
            raise HTTPException(status_code=409, detail="در اجرای انتخاب‌شده job فعال Codex وجود ندارد؛ برای جلوگیری از لغو gate یا مرحلهٔ نامرتبط، عملیات متوقف شد.")
    cancelled = _run([gh, "run", "cancel", str(trusted_run_id), "--repo", _repository()], timeout=12)
    if not cancelled or cancelled.returncode != 0:
        raise HTTPException(status_code=502, detail="لغو اجرای انتخاب‌شده توسط GitHub تأیید نشد.")
    return {"accepted": True, "run_id": trusted_run_id, "message": f"درخواست لغو اجرای #{trusted_run_id} به GitHub ارسال شد."}


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


def _cached_run_diagnostics(run_id: int) -> dict[str, Any] | None:
    # Preserve the legacy monkeypatch seam while keeping cache ownership in
    # the run-history module.
    _run_history._DIAGNOSTICS_CACHE = _RUN_DIAGNOSTICS_CACHE
    return _run_history.cached_run_diagnostics(
        run_id, read_diagnostics=_read_run_diagnostics
    )


def _workflow_runs(gh: str) -> list[dict[str, Any]]:
    return _run_history.workflow_runs(
        gh,
        workflow=_WORKFLOW,
        github_api_json=_github_api_json,
        cached_diagnostics=_cached_run_diagnostics,
    )


def _verified_workflow_run(gh: str, run_id: int) -> dict[str, Any]:
    return _run_history.verified_workflow_run(
        gh, run_id, workflow=_WORKFLOW, github_api_json=_github_api_json
    )


def _read_run_diagnostics(run_id: int) -> dict[str, Any] | None:
    return _run_history.read_run_diagnostics(
        run_id,
        read_payload=read_run_diagnostics,
        sanitize_log=_sanitize_workflow_log,
        max_entries=_MAX_RUN_DIAGNOSTIC_ENTRIES,
    )


def _workflow_failure_summary(value: str, failures: list[dict[str, Any]]) -> str | None:
    return _run_log_safety.workflow_failure_summary(
        value, failures, sanitize_log=_sanitize_workflow_log
    )


def _sanitize_workflow_log(value: str) -> str:
    return _run_log_safety.sanitize_workflow_log(
        value, max_chars=_MAX_DASHBOARD_LOG_CHARS
    )


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
    logs_result = _run(
        [gh, "run", "view", str(trusted_run_id), "--log", "--repo", _repository()],
        timeout=45,
    )
    raw_log = ""
    if logs_result and logs_result.returncode == 0:
        raw_log = logs_result.stdout or ""
    elif logs_result:
        raw_log = logs_result.stdout or logs_result.stderr or ""
    diagnostic = _read_run_diagnostics(trusted_run_id)
    return _run_logs_backend.build_run_logs_payload(
        run=run,
        jobs=jobs,
        raw_log=raw_log,
        diagnostic=diagnostic,
        max_log_chars=_MAX_DASHBOARD_LOG_CHARS,
        sanitize_log=_sanitize_workflow_log,
        failure_summary=_workflow_failure_summary,
    )

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

    # Absolute cleanup paths can disclose usernames and host directory layout.
    failed_files = sorted({Path(str(item).replace(chr(92), "/")).name for item in failed_files if str(item).strip()})

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
    def ensure_no_active_workflow() -> None:
        gh = _authenticated_gh()
        runs = _workflow_runs(gh)
        if any(item.get("status") in _ACTIVE_RUN_STATES for item in runs):
            raise HTTPException(
                status_code=409,
                detail="عامل هنوز اجرای فعال یا در صف دارد؛ پاک‌سازی داده‌های موقت تا پایان اجرا مجاز نیست.",
            )

    try:
        if _is_windows():
            # The Broker owns local repository/state/checkpoint locks, but it
            # cannot independently authenticate the remote GitHub workflow
            # state. Check remote admission before asking it to reconcile local
            # progress; an active/queued workflow must never be marked stale
            # merely because its local controller process has exited.
            ensure_no_active_workflow()
            return _cleanup_autonomous_engineering_temporary_data_locked()
        ensure_no_active_workflow()
        # POSIX uses the same repository lock as the Runner, then the helper
        # acquires durable-state and checkpoint-store locks in that order.
        with repository_execution_lock("ui-cleanup", "1"):
            ensure_no_active_workflow()
            return _cleanup_autonomous_engineering_temporary_data_locked()
    except StateBrokerConflict as exc:
        blockers = set(getattr(exc, "blockers", []) or [])
        if blockers.intersection({"active_progress_run", "repository_execution_active", "pending_checkpoint", "checkpoint_store_busy"}):
            raise HTTPException(
                status_code=409,
                detail="Broker وضعیت اجرای فعال یا checkpoint را زیر قفل تأیید کرد؛ پاک‌سازی انجام نشد.",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="Broker نتوانست وضعیت پاک‌سازی را به‌صورت امن تأیید کند.",
        ) from exc
    except RuntimeError as exc:
        if "Another autonomous execution owns this repository worktree" in str(exc) or "repository lock busy" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="اجرای دیگری مالک worktree است؛ پاک‌سازی تا آزادشدن قفل انجام نمی‌شود.",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=f"قفل یا پاک‌سازی امن فضای عامل در دسترس نیست ({type(exc).__name__}).",
        ) from exc
