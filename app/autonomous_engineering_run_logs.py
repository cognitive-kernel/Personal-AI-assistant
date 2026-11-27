"""Workflow run-log response assembly, independent of GitHub transport."""

from __future__ import annotations

from typing import Any, Callable


def build_run_logs_payload(
    *,
    run: dict[str, Any],
    jobs: list[Any],
    raw_log: str,
    diagnostic: dict[str, Any] | None,
    max_log_chars: int,
    sanitize_log: Callable[[str], str],
    failure_summary: Callable[[str, list[dict[str, Any]]], str | None],
) -> dict[str, Any]:
    """Build the dashboard payload from already retrieved and verified run data."""
    failures: list[dict[str, Any]] = []
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
    if not raw_log and run.get("status") != "completed":
        raw_log = "گزارش کامل تا پایان اجرای Workflow در دسترس نیست."
    elif not raw_log and run.get("status") == "completed":
        raw_log = "GitHub گزارشی برای این اجرا برنگرداند؛ جزئیات مرحله‌های ناموفق در بالا آمده است."
    diagnostic_entries = diagnostic.get("diagnostics", []) if diagnostic else []
    latest_diagnostic = diagnostic_entries[-1] if diagnostic_entries else None
    error_summary = (
        latest_diagnostic.get("summary")
        if latest_diagnostic else failure_summary(raw_log, failures)
    )
    return {
        "run": {
            "id": run["id"],
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
        "logs": sanitize_log(raw_log),
        "truncated": len(raw_log) > max_log_chars,
    }
