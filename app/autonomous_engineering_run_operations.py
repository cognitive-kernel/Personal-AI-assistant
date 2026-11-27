"""Application service for verified GitHub workflow log and deletion operations."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from . import autonomous_engineering_run_logs as _run_logs_backend

_STATUS_LOGGER = logging.getLogger(__name__)

def run_logs(run_id: int, *, _authenticated_gh, _verified_workflow_run, _github_api_json, _run, _repository, _read_run_diagnostics, _MAX_DASHBOARD_LOG_CHARS, _sanitize_workflow_log, _workflow_failure_summary) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, run_id)
    trusted_run_id = int(run["id"])
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

def delete_run(run_id: int, *, _authenticated_gh, _verified_workflow_run, _run, _repository) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, run_id)
    trusted_run_id = int(run["id"])
    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="فقط اجرای پایان‌یافته قابل حذف است؛ اجرای فعال حفظ شد.")
    result = _run(
        [gh, "api", "--method", "DELETE", f"repos/{_repository()}/actions/runs/{trusted_run_id}"],
        timeout=20,
    )
    if not result or result.returncode != 0:
        _STATUS_LOGGER.warning(
            "GitHub run deletion failed; returncode=%s",
            result.returncode if result else None,
        )
        raise HTTPException(
            status_code=502,
            detail="حذف اجرای GitHub تأیید نشد؛ وضعیت سرویس را بازخوانی کنید.",
        )
    return {"deleted": True, "run_id": trusted_run_id, "message": "اجرای پایان‌یافته از تاریخچهٔ GitHub حذف شد."}
