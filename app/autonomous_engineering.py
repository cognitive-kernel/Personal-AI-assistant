"""Local diagnostics and explicit controls for the Ollama-backed GitHub engineering agent."""
from __future__ import annotations

import csv
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt, StrictStr

from .task_routes import require_loopback_client
from .autonomous_engineering_progress import get_progress, pending_work_status, storage_access_status
from .autonomous_engineering_storage import cleanup_storage, inspect_storage
from .ollama_context import maximum_context_tokens, supports_tool_calling

_RUN_PROGRESS_CACHE: dict[str, Any] = {"run_id": None, "at": 0.0, "value": None}
_RUN_LIST_CACHE: dict[str, Any] = {"at": 0.0, "value": None}

router = APIRouter(
    prefix="/api/autonomous-engineering",
    tags=["autonomous-engineering"],
    dependencies=[Depends(require_loopback_client)],
)

_DEFAULT_REPOSITORY = "cognitive-kernel/Personal-AI-assistant"
_WORKFLOW = "autonomous-codex-engineering.yml"
_DEFAULT_INTERVAL_HOURS = 24
_ALLOWED_INTERVAL_HOURS = {1, 2, 4, 6, 12, 24}
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_CODEX_CONTEXT_VARIANT_PATTERN = re.compile(r"-codex-ctx[0-9]+$", re.IGNORECASE)


def _is_codex_context_variant(model: str) -> bool:
    return bool(_CODEX_CONTEXT_VARIANT_PATTERN.search(model.strip()))


class EngineeringSettings(BaseModel):
    model: StrictStr
    interval_hours: StrictInt
    execution_mode: StrictStr = "continuous"


class ManualTaskRequest(BaseModel):
    task: StrictStr = Field(default="", max_length=2000)


class RunControlRequest(BaseModel):
    run_id: StrictStr = Field(min_length=1, max_length=24)


def _repository() -> str:
    value = os.getenv("AUTONOMOUS_ENGINEERING_REPOSITORY", _DEFAULT_REPOSITORY).strip()
    if "/" not in value or any(part in value for part in (" ", "\\", "..")):
        return _DEFAULT_REPOSITORY
    owner, name = value.split("/", 1)
    return value if owner and name and "/" not in name else _DEFAULT_REPOSITORY


def _run(command: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None



# Service implementations live in a separate module; wrappers preserve the public API and test seams.
from . import autonomous_engineering_services as _services


_NoRedirectHandler = _services.NoRedirectHandler


def _ollama_status(model: str) -> dict[str, Any]:
    from .ollama import _find_running_model_entry, _request as ollama_request, model_details

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
        recommended_context_fn=lambda selected_model: model_details(selected_model).get("recommended_context_tokens"),
        tool_probe_fn=_tool_probe,
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


def _application_windows_identity() -> dict[str, str] | None:
    """Return the current app process identity without relying on a configured username."""
    if platform.system().casefold() != "windows":
        return None
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        row = next(csv.reader(result.stdout.splitlines()), [])
        if len(row) < 2 or not row[0].strip().lstrip("\ufeff") or not re.fullmatch(r"S-1-[0-9-]+", row[1].strip()):
            return None
        return {"name": row[0].strip().lstrip("\ufeff"), "sid": row[1].strip()}
    except (OSError, subprocess.SubprocessError, csv.Error, ValueError):
        return None


def collect_status() -> dict[str, Any]:
    github = _github_snapshot()
    settings = github.get("settings", {})
    model = settings.get("model", "")
    interval_hours = settings.get("interval_hours", _DEFAULT_INTERVAL_HOURS)
    execution_mode = settings.get("execution_mode", "continuous")
    paused = bool(settings.get("paused", False))
    ollama = _ollama_status(model)
    codex = _codex_status()
    runner = _runner_status()
    storage_status = storage_access_status()
    progress_storage = storage_status["available"]
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
        "storage": storage_status,
        "application_identity": _application_windows_identity(),
        "platform": platform.platform(),
        "local_only_inference": True,
    }


@router.get("/status")
def autonomous_engineering_status() -> dict[str, Any]:
    return collect_status()


def _enrich_progress_from_github(current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(current, dict):
        return current
    run_id = str(current.get("run_id", ""))
    if not run_id.isdigit():
        return current
    updated = dict(current)
    if updated.get("status") == "failed" and not isinstance(updated.get("diagnostic"), dict):
        updated["diagnostic"] = {
            "run_id": run_id,
            "job": "جزئیات Job در دسترس نیست",
            "step": "مرحلهٔ دقیق شکست هنوز دریافت نشده است",
            "job_url": updated.get("run_url"),
            "message": "GitHub اجرای ناموفق را گزارش کرده است؛ جزئیات Job از API قابل دریافت نبود.",
        }
    now = time.monotonic()
    if _RUN_PROGRESS_CACHE.get("run_id") == run_id and now - float(_RUN_PROGRESS_CACHE.get("at", 0)) < 12:
        cached = _RUN_PROGRESS_CACHE.get("value")
        return dict(cached) if isinstance(cached, dict) else current
    gh = shutil.which("gh")
    if not gh:
        return updated
    result = _run(
        [gh, "api", f"repos/{_repository()}/actions/runs/{run_id}/jobs?per_page=100"],
        timeout=8,
    )
    if not result or result.returncode != 0:
        return updated
    try:
        jobs = json.loads(result.stdout).get("jobs", [])
    except (ValueError, AttributeError):
        return updated
    if not isinstance(jobs, list) or not jobs:
        return updated
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
        failed_steps = failed_job.get("steps", [])
        failed_steps = failed_steps if isinstance(failed_steps, list) else []
        failed_step = next((step for step in failed_steps if isinstance(step, dict) and step.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}), None)
        diagnostic = dict(updated.get("diagnostic")) if isinstance(updated.get("diagnostic"), dict) else {}
        diagnostic.update({
            "run_id": run_id,
            "job": str(failed_job.get("name", "نامشخص"))[:180],
            "job_url": failed_job.get("html_url") if isinstance(failed_job.get("html_url"), str) else None,
            "step": str(failed_step.get("name", "مرحلهٔ دقیق از GitHub قابل تشخیص نیست"))[:240] if failed_step else None,
            "step_conclusion": failed_step.get("conclusion") if failed_step else None,
            "job_message": "گزارش کامل مرحله در GitHub Actions در دسترس است.",
        })
        diagnostic.setdefault("message", "GitHub نام Job و مرحلهٔ ناموفق را مشخص کرده است؛ برای خروجی کامل، گزارش همان Job را باز کنید.")
        updated.update({
            "status": "failed",
            "phase_label": "چرخه با خطا پایان یافت",
            "activity": str(failed_job.get("name", "مرحلهٔ ناموفق")) + ": " + str(failed_job.get("conclusion", "failed")),
            "diagnostic": diagnostic,
        })
    elif queued_job and queued_job.get("name") == "codex":
        updated.update({
            "phase": "queued", "phase_label": "در انتظار Runner ویندوز",
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


def _recent_github_runs() -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _RUN_LIST_CACHE.get("value")
    if isinstance(cached, list) and now - float(_RUN_LIST_CACHE.get("at", 0)) < 15:
        return cached
    snapshot = _github_snapshot()
    runs = snapshot.get("recent_runs", []) if snapshot.get("runs_available") else []
    runs = runs if isinstance(runs, list) else []
    _RUN_LIST_CACHE.update({"at": now, "value": runs})
    return runs


def _timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@router.get("/progress")
def autonomous_engineering_progress(
    period: str = "daily", start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    result = get_progress(period=period, start_date=start_date, end_date=end_date)
    current = result.get("current")
    if not isinstance(current, dict) or current.get("status") not in {"running", "queued"}:
        failed_run = next(
            (
                run for run in _recent_github_runs()
                if isinstance(run, dict)
                and run.get("status") == "completed"
                and run.get("conclusion") in {"failure", "timed_out", "action_required"}
            ),
            None,
        )
        if failed_run:
            current_time = _timestamp(current.get("updated_at")) if isinstance(current, dict) else 0.0
            failed_time = _timestamp(failed_run.get("created_at"))
            same_run = isinstance(current, dict) and str(current.get("run_id")) == str(failed_run.get("id"))
            if not same_run and failed_time > current_time:
                current = {
                    "run_id": str(failed_run.get("id")),
                    "status": "failed",
                    "phase": "failed",
                    "phase_label": "اجرای عامل با خطا پایان یافت",
                    "activity": "در حال دریافت مرحلهٔ دقیق شکست از GitHub Actions",
                    "updated_at": failed_run.get("created_at"),
                    "run_url": failed_run.get("url"),
                }
    result["current"] = _enrich_progress_from_github(current)
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
def dispatch_autonomous_engineering(payload: ManualTaskRequest | None = None) -> dict[str, Any]:
    manual_task = payload.task.strip() if payload else ""
    if "\x00" in manual_task:
        raise HTTPException(status_code=422, detail="متن وظیفه شامل نویسهٔ نامعتبر است.")
    readiness = collect_status()
    if not readiness.get("github", {}).get("runs_available", False):
        raise HTTPException(status_code=503, detail="وضعیت اجراهای GitHub قابل دریافت نیست؛ اجرای جدید به‌صورت ایمن متوقف شد.")
    if readiness.get("paused", False):
        raise HTTPException(status_code=409, detail="صف مهندسی در وضعیت توقف ایمن است؛ ابتدا ادامه را فعال کنید.")
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail="پیش‌نیازهای عامل کامل نیست؛ ابتدا وضعیت محلی را بررسی کنید.")
    if manual_task:
        pending = pending_work_status()
        if not pending.get("available"):
            raise HTTPException(
                status_code=503,
                detail="وضعیت تسک پایدار قابل بررسی نیست؛ برای جلوگیری از تداخل، وظیفهٔ جدید ارسال نشد.",
            )
        if pending.get("blocked"):
            raise HTTPException(
                status_code=409,
                detail="یک تسک، PR یا عملیات پاک‌سازی قبلی هنوز تعیین‌تکلیف نشده است؛ متن جدید پذیرفته نشد. برای ادامهٔ همان کار، اجرای بدون متن وظیفه را ارسال کنید.",
            )
    active_runs = readiness.get("github", {}).get("recent_runs", [])
    if any(item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"} for item in active_runs):
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
    result = _run(
        [
            gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main",
            "--field", f"manual_task={manual_task}",
        ],
        timeout=20,
    )
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="درخواست اجرای Workflow به GitHub ارسال نشد.")
    return {
        "accepted": True,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "manual_task_submitted": bool(manual_task),
        "message": (
            "وظیفهٔ دستی ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید."
            if manual_task else
            "درخواست اجرای چرخهٔ خودکار ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید."
        ),
    }


@router.get("/storage")
def autonomous_engineering_storage() -> dict[str, Any]:
    return inspect_storage()


@router.post("/storage/cleanup")
def cleanup_autonomous_engineering_storage() -> dict[str, Any]:
    status = _github_snapshot()
    runs = status.get("recent_runs", [])
    active = any(
        isinstance(item, dict) and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        for item in runs
    )
    if not status.get("runs_available", False):
        raise HTTPException(status_code=503, detail="وضعیت اجراهای GitHub قابل تأیید نیست؛ پاک‌سازی برای حفظ فایل‌های اجرای فعال متوقف شد.")
    return cleanup_storage(active_run=active)


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
def resume_autonomous_engineering() -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="دسترسی GitHub در حساب برنامه آماده نیست.")
    result = _run([gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "false", "--repo", _repository()], timeout=15)
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="ثبت ادامه در GitHub ناموفق بود.")
    dispatched = _run([gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main"], timeout=20)
    if not dispatched or dispatched.returncode != 0:
        raise HTTPException(status_code=502, detail="ادامه ثبت شد اما اجرای چرخه به GitHub ارسال نشد.")
    return {"accepted": True, "paused": False, "message": "ادامه از وضعیت ذخیره‌شده درخواست شد."}


@router.post("/cancel")
def cancel_autonomous_engineering(payload: RunControlRequest) -> dict[str, Any]:
    if not payload.run_id.isdigit():
        raise HTTPException(status_code=422, detail="شناسهٔ اجرای انتخاب‌شده معتبر نیست.")
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
    active = next(
        (
            item for item in runs
            if isinstance(item, dict)
            and str(item.get("databaseId", "")) == payload.run_id
            and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        ),
        None,
    )
    if not active:
        raise HTTPException(status_code=409, detail="اجرای انتخاب‌شده فعال نیست یا در فهرست اجراهای اخیر وجود ندارد؛ وضعیت را بازخوانی کنید.")
    cancelled = _run(
        [gh, "run", "cancel", payload.run_id, "--repo", _repository()],
        timeout=12,
    )
    if not cancelled or cancelled.returncode != 0:
        raise HTTPException(status_code=502, detail="لغو اجرا توسط GitHub تأیید نشد.")
    return {"accepted": True, "run_id": payload.run_id, "message": f"درخواست لغو اجرای {payload.run_id} به GitHub ارسال شد."}

 
@router.delete("/runs/{run_id}")
def delete_autonomous_engineering_run(run_id: str) -> dict[str, Any]:
    if not run_id.isdigit() or len(run_id) > 24:
        raise HTTPException(status_code=422, detail="شناسهٔ اجرا معتبر نیست.")
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    viewed = _run([gh, "api", f"repos/{_repository()}/actions/runs/{run_id}"], timeout=12)
    if not viewed or viewed.returncode != 0:
        raise HTTPException(status_code=502, detail="وضعیت اجرای انتخاب‌شده قابل دریافت نیست؛ سابقه حذف نشد.")
    try:
        state = json.loads(viewed.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="پاسخ وضعیت اجرا معتبر نیست؛ سابقه حذف نشد.") from exc
    if not isinstance(state, dict) or state.get("status") != "completed":
        raise HTTPException(status_code=409, detail="فقط اجرای پایان‌یافته قابل حذف است؛ اجرای فعال یا در صف حفظ شد.")
    if state.get("path") != f".github/workflows/{_WORKFLOW}":
        raise HTTPException(status_code=403, detail="فقط سوابق گردش‌کار عامل مهندسی خودکار از این صفحه قابل حذف است.")
    deleted = _run([gh, "run", "delete", run_id, "--repo", _repository()], timeout=15)
    if not deleted or deleted.returncode != 0:
        raise HTTPException(status_code=502, detail="GitHub حذف سابقهٔ اجرا را تأیید نکرد.")
    return {"deleted": True, "run_id": run_id, "message": f"سابقهٔ اجرای {run_id} از GitHub Actions حذف شد؛ فایل‌های کاری و checkpoint محلی تغییر نکردند."}
