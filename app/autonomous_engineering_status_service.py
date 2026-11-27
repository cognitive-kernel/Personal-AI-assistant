"""Bounded, independent readiness-probe scheduling and status orchestration."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

from . import autonomous_engineering_readiness as _readiness_backend
from .autonomous_engineering_native_config import native_state_enabled

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

def submit_status_probe(function, *, _STATUS_EXECUTOR, _STATUS_EXECUTOR_SLOTS):
    """Submit a probe only when a bounded worker/queue slot is available."""
    if not _STATUS_EXECUTOR_SLOTS.acquire(blocking=False):
        return None
    try:
        future = _STATUS_EXECUTOR.submit(function)
    except RuntimeError:
        _STATUS_EXECUTOR_SLOTS.release()
        _STATUS_LOGGER.warning("Autonomous Engineering status probe could not be scheduled")
        return None
    except Exception:
        _STATUS_EXECUTOR_SLOTS.release()
        raise
    future.add_done_callback(lambda _future: _STATUS_EXECUTOR_SLOTS.release())
    return future


def resolve_status_probe(
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
        _STATUS_LOGGER.warning(
            "Autonomous Engineering status probe failed: %s (%s)",
            name,
            type(exc).__name__,
        )
        return fallback


def status_ollama_fallback(model: str, *, _is_codex_context_variant) -> dict[str, Any]:
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


def collect_status(*, _STATUS_PROBE_TIMEOUT_SECONDS, _GITHUB_STATUS_PROBE_TIMEOUT_SECONDS, _submit_status_probe, _resolve_status_probe, _github_snapshot, _codex_status, _runner_status, _storage_and_pending_work_status, _DEFAULT_INTERVAL_HOURS, _STATUS_PROBE_CONTEXT, _ollama_status, _status_ollama_fallback, _repository, _WORKFLOW, _is_codex_context_variant, which, platform_name) -> dict[str, Any]:
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
        "cli_installed": bool(which("gh")),
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
    if not isinstance(github_settings, dict) or (
        github.get("authenticated") is True and github.get("settings_available") is False
    ):
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

    status = _readiness_backend.build_status_payload(
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
        platform_name=platform_name,
        timed_out=timed_out,
        errors=errors,
        unavailable=unavailable,
        default_interval_hours=_DEFAULT_INTERVAL_HOURS,
        is_codex_context_variant=_is_codex_context_variant,
    )
    if "github_settings" in errors:
        status["ready"] = False
        status["can_dispatch"] = False
    if native_state_enabled():
        status["state_backend"] = "native"
        status["checks"]["native_runner_workflow_compatible"] = False
        status["ready"] = False
        status["can_dispatch"] = False
    else:
        status["state_backend"] = "legacy_broker"
    return status
