"""Pure GitHub workflow-job to dashboard progress projection."""

from __future__ import annotations

from typing import Any


def enrich_progress_payload(
    current: dict[str, Any],
    jobs: list[Any],
) -> dict[str, Any]:
    """Project verified GitHub jobs onto a copy of the current progress payload."""
    updated = dict(current)
    active_job = next(
        (job for job in jobs if isinstance(job, dict) and job.get("status") == "in_progress"),
        None,
    )
    failed_job = next(
        (
            job for job in jobs
            if isinstance(job, dict)
            and job.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}
        ),
        None,
    )
    queued_job = next(
        (
            job for job in jobs
            if isinstance(job, dict) and job.get("status") == "queued" and job.get("name") == "codex"
        ),
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
    elif queued_job:
        updated.update({
            "phase": "queued",
            "phase_label": "در انتظار Runner خودمیزبان",
            "activity": "چرخه در صف اجرا قرار دارد",
            "status": "queued",
        })
    elif all(
        isinstance(job, dict) and job.get("status") == "completed"
        for job in jobs
    ):
        conclusions = {job.get("conclusion") for job in jobs if isinstance(job, dict)}
        if conclusions.issubset({"success", "skipped", "neutral"}):
            updated.update({
                "status": "completed",
                "phase": "complete",
                "phase_label": "چرخهٔ مهندسی کامل شد",
                "activity": "تمام مراحل Workflow با موفقیت پایان یافتند",
                "percent": 100,
            })
    return updated
