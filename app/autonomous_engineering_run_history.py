"""GitHub workflow-run history and persisted diagnostic normalization."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from fastapi import HTTPException


_DIAGNOSTICS_CACHE: dict[int, tuple[float, dict[str, Any] | None]] = {}
_DIAGNOSTICS_CACHE_LOCK = threading.Lock()
_DIAGNOSTICS_CACHE_TTL_SECONDS = 60.0
_DIAGNOSTICS_CACHE_MAX_ENTRIES = 256


def cached_run_diagnostics(
    run_id: int, *, read_diagnostics: Callable[[int], dict[str, Any] | None]
) -> dict[str, Any] | None:
    """Bound and expire the per-run diagnostic cache."""
    now = time.monotonic()
    with _DIAGNOSTICS_CACHE_LOCK:
        cached = _DIAGNOSTICS_CACHE.get(run_id)
        if cached is not None and now - cached[0] < _DIAGNOSTICS_CACHE_TTL_SECONDS:
            return cached[1]
    diagnostic = read_diagnostics(run_id)
    now = time.monotonic()
    with _DIAGNOSTICS_CACHE_LOCK:
        _DIAGNOSTICS_CACHE[run_id] = (now, diagnostic)
        expired = [
            key for key, (cached_at, _value) in _DIAGNOSTICS_CACHE.items()
            if now - cached_at >= _DIAGNOSTICS_CACHE_TTL_SECONDS
        ]
        for key in expired:
            _DIAGNOSTICS_CACHE.pop(key, None)
        while len(_DIAGNOSTICS_CACHE) > _DIAGNOSTICS_CACHE_MAX_ENTRIES:
            _DIAGNOSTICS_CACHE.pop(next(iter(_DIAGNOSTICS_CACHE)))
    return diagnostic


def workflow_runs(
    gh: str,
    *,
    workflow: str,
    github_api_json: Callable[..., dict[str, Any]],
    cached_diagnostics: Callable[[int], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    # Keep the CLI/API command shape fixed. HTTP query parameters must never
    # become command-line arguments through the GitHub CLI wrapper.
    data = github_api_json(
        gh, f"actions/workflows/{workflow}/runs?per_page=100", timeout=20
    )
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise HTTPException(status_code=502, detail="فهرست اجراهای Workflow در پاسخ GitHub وجود ندارد.")
    normalized: list[dict[str, Any]] = []
    for item in runs:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        diagnostic = (
            cached_diagnostics(item["id"])
            if item.get("status") == "completed" and item.get("conclusion") in {
                "failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"
            }
            else None
        )
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


def verified_workflow_run(
    gh: str,
    run_id: int,
    *,
    workflow: str,
    github_api_json: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise HTTPException(status_code=422, detail="شناسهٔ اجرای GitHub معتبر نیست.")
    run = github_api_json(gh, f"actions/runs/{run_id}", timeout=12)
    expected_path = f".github/workflows/{workflow}"
    workflow_path = str(run.get("path") or "").split("@", 1)[0]
    verified_run_id = run.get("id")
    if (
        isinstance(verified_run_id, bool)
        or not isinstance(verified_run_id, int)
        or verified_run_id <= 0
        or verified_run_id != run_id
        or workflow_path != expected_path
    ):
        raise HTTPException(status_code=404, detail="اجرای درخواستی متعلق به عامل مهندسی خودکار نیست.")
    return {
        "id": verified_run_id,
        "status": str(run.get("status") or "unknown"),
        "conclusion": run.get("conclusion"),
        "workflow_path": workflow_path,
        "url": run.get("html_url") if isinstance(run.get("html_url"), str) else None,
    }


def read_run_diagnostics(
    run_id: int,
    *,
    read_payload: Callable[[int], Any],
    sanitize_log: Callable[[str], str],
    max_entries: int = 8,
) -> dict[str, Any] | None:
    """Read and validate the runner-persisted redacted report for one run ID."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    try:
        payload = read_payload(run_id)
        if not isinstance(payload, dict):
            return None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    if (
        payload.get("schema_version") != 1
        or str(payload.get("run_id")) != str(run_id)
        or not isinstance(payload.get("diagnostics"), list)
    ):
        return None
    entries = []
    for item in payload["diagnostics"][-max_entries:]:
        if not isinstance(item, dict):
            continue
        entries.append({
            "run_attempt": str(item.get("run_attempt") or "")[:12],
            "task": item.get("task") if isinstance(item.get("task"), int) else None,
            "attempt": item.get("attempt") if isinstance(item.get("attempt"), int) else None,
            "stage": sanitize_log(str(item.get("stage") or ""))[:80],
            "category": sanitize_log(str(item.get("category") or ""))[:80],
            "summary": sanitize_log(str(item.get("summary") or ""))[:800],
            "detail": sanitize_log(str(item.get("detail") or ""))[-12_000:],
            "recorded_at": str(item.get("recorded_at") or "")[:64],
        })
    if not entries:
        return None
    return {
        "run_id": run_id,
        "updated_at": str(payload.get("updated_at") or "")[:64],
        "diagnostics": entries,
    }
