"""Local diagnostics and explicit controls for the Ollama-backed GitHub engineering agent."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import re
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictInt, StrictStr

from .task_routes import require_loopback_client
from .autonomous_engineering_progress import get_progress, storage_accessible

_RUN_PROGRESS_CACHE: dict[str, Any] = {"run_id": None, "at": 0.0, "value": None}

router = APIRouter(
    prefix="/api/autonomous-engineering",
    tags=["autonomous-engineering"],
    dependencies=[Depends(require_loopback_client)],
)

_DEFAULT_REPOSITORY = "cognitive-kernel/Personal-AI-assistant"
_WORKFLOW = "autonomous-codex-engineering.yml"
_DEFAULT_MODEL = "gpt-oss:20b"
_DEFAULT_RUNS_PER_DAY = 1
_ALLOWED_RUNS_PER_DAY = {1, 2, 3, 4, 6, 8, 12, 24}
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")


class EngineeringSettings(BaseModel):
    model: StrictStr
    runs_per_day: StrictInt


def _repository() -> str:
    value = os.getenv("AUTONOMOUS_ENGINEERING_REPOSITORY", _DEFAULT_REPOSITORY).strip()
    if "/" not in value or any(part in value for part in (" ", "\\", "..")):
        return _DEFAULT_REPOSITORY
    owner, name = value.split("/", 1)
    return value if owner and name and "/" not in name else _DEFAULT_REPOSITORY


def _model() -> str:
    value = os.getenv("AUTONOMOUS_ENGINEERING_MODEL", _DEFAULT_MODEL).strip()
    return value[:120] if value else _DEFAULT_MODEL


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


def _ollama_status(model: str) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/tags",
            headers={"Accept": "application/json"},
        )
        # Local Ollama metadata must never be sent through an environment proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = sorted(
            str(item.get("name", ""))
            for item in models
            if isinstance(item, dict) and item.get("name")
        )
        installed = model in names
        return {
            "online": True,
            "model": model,
            "model_installed": installed,
            "models": names[:40],
        }
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        return {"online": False, "model": model, "model_installed": False, "models": []}


def _codex_status() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {"installed": False, "version": None}
    result = _run([executable, "--version"], timeout=5)
    version = (result.stdout or "").strip()[:120] if result and result.returncode == 0 else None
    return {"installed": bool(version), "version": version}


def _runner_status() -> dict[str, Any]:
    found = False
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").casefold()
            command = " ".join(process.info.get("cmdline") or []).casefold()
            if "runner.listener" in name or "runner.listener" in command:
                found = True
                break
        except (psutil.Error, OSError):
            continue
    return {"running": found, "process_detected": found}


def _github_snapshot() -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        return {
            "cli_installed": False,
            "authenticated": False,
            "recent_runs": [],
            "runs_available": False,
        }
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    authenticated = bool(auth and auth.returncode == 0)
    recent_runs: list[dict[str, Any]] = []
    runs_available = False
    settings = {"model": _model(), "runs_per_day": _DEFAULT_RUNS_PER_DAY}
    if authenticated:
        result = _run(
            [
                gh, "run", "list", "--repo", _repository(), "--workflow", _WORKFLOW,
                "--limit", "5", "--json",
                "databaseId,status,conclusion,createdAt,url,headBranch",
            ],
            timeout=12,
        )
        if result and result.returncode == 0:
            try:
                parsed = json.loads(result.stdout)
                if isinstance(parsed, list):
                    recent_runs = [
                        {
                            "id": item.get("databaseId"),
                            "status": item.get("status"),
                            "conclusion": item.get("conclusion"),
                            "created_at": item.get("createdAt"),
                            "url": item.get("url"),
                            "branch": item.get("headBranch"),
                        }
                        for item in parsed[:5]
                        if isinstance(item, dict)
                    ]
                    runs_available = True
            except ValueError:
                recent_runs = []
        variables = _run(
            [gh, "variable", "list", "--repo", _repository(), "--json", "name,value"],
            timeout=12,
        )
        if variables and variables.returncode == 0:
            try:
                values = json.loads(variables.stdout)
                if isinstance(values, list):
                    configured = {
                        str(item.get("name")): str(item.get("value", ""))
                        for item in values if isinstance(item, dict)
                    }
                    candidate_model = configured.get("AUTONOMOUS_ENGINEERING_MODEL", "")
                    candidate_runs = configured.get("AUTONOMOUS_ENGINEERING_RUNS_PER_DAY", "")
                    if _MODEL_PATTERN.fullmatch(candidate_model):
                        settings["model"] = candidate_model
                    if candidate_runs.isdigit() and int(candidate_runs) in _ALLOWED_RUNS_PER_DAY:
                        settings["runs_per_day"] = int(candidate_runs)
            except (ValueError, TypeError):
                pass
    return {
        "cli_installed": True,
        "authenticated": authenticated,
        "recent_runs": recent_runs,
        "runs_available": runs_available,
        "settings": settings,
    }

def collect_status() -> dict[str, Any]:
    github = _github_snapshot()
    settings = github.get("settings", {})
    model = settings.get("model", _model())
    runs_per_day = settings.get("runs_per_day", _DEFAULT_RUNS_PER_DAY)
    ollama = _ollama_status(model)
    codex = _codex_status()
    runner = _runner_status()
    progress_storage = storage_accessible()
    checks = {
        "ollama_online": ollama["online"],
        "model_installed": ollama["model_installed"],
        "codex_installed": codex["installed"],
        "runner_running": runner["running"],
        "github_cli_authenticated": github["authenticated"],
        "github_runs_available": github.get("runs_available", False),
        "progress_storage_accessible": progress_storage,
    }
    return {
        "ready": all(value for key, value in checks.items() if key != "codex_installed"),
        "checks": checks,
        "ollama": ollama,
        "codex": codex,
        "runner": runner,
        "github": github,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "model": model,
        "models": ollama.get("models", []),
        "runs_per_day": runs_per_day,
        "progress_storage_accessible": progress_storage,
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
        (job for job in jobs if isinstance(job, dict) and job.get("status") == "queued" and job.get("name") in {"validate", "open-draft-pr"}),
        None,
    )
    if active_job:
        name = str(active_job.get("name", ""))
        steps = active_job.get("steps", [])
        steps = steps if isinstance(steps, list) else []
        completed = sum(isinstance(step, dict) and step.get("status") == "completed" for step in steps)
        total = max(1, len(steps))
        active_step = next(
            (step for step in steps if isinstance(step, dict) and step.get("status") == "in_progress"),
            None,
        )
        step_name = str(active_step.get("name", "در حال اجرای مرحله")) if active_step else "در انتظار مرحلهٔ بعد"
        if name == "validate":
            updated.update({
                "phase": "validation", "phase_label": "اعتبارسنجی مستقل روی GitHub",
                "percent": 92 + (5 * completed // total),
                "activity": step_name[:180], "status": "running",
            })
        elif name == "open-draft-pr":
            updated.update({
                "phase": "publication", "phase_label": "انتشار پیش‌نویس Pull Request",
                "percent": 98 + (completed // total),
                "activity": step_name[:180], "status": "running",
            })
        else:
            updated["activity"] = step_name[:180]
    elif failed_job:
        updated.update({
            "status": "failed",
            "phase_label": "چرخه با خطا پایان یافت",
            "activity": str(failed_job.get("name", "مرحلهٔ ناموفق")) + ": " + str(failed_job.get("conclusion", "failed")),
        })
    elif queued_job and queued_job.get("name") == "validate":
        updated.update({
            "phase": "validation", "phase_label": "در انتظار اعتبارسنجی مستقل",
            "percent": 92, "activity": "منتظر شروع job اعتبارسنجی روی GitHub", "status": "queued",
        })
    elif queued_job and queued_job.get("name") == "open-draft-pr":
        updated.update({
            "phase": "publication", "phase_label": "در انتظار انتشار پیش‌نویس",
            "percent": 97, "activity": "اعتبارسنجی تمام شده؛ منتظر مرحلهٔ انتشار PR", "status": "queued",
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


@router.post("/settings", status_code=200)
def save_autonomous_engineering_settings(payload: EngineeringSettings) -> dict[str, Any]:
    model = payload.model.strip()
    if not _MODEL_PATTERN.fullmatch(model):
        raise HTTPException(status_code=422, detail="شناسهٔ مدل Ollama معتبر نیست.")
    if payload.runs_per_day not in _ALLOWED_RUNS_PER_DAY:
        raise HTTPException(status_code=422, detail="تعداد اجرا باید یکی از مقادیر مجاز روزانه باشد.")
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    updates = (
        ("AUTONOMOUS_ENGINEERING_MODEL", model),
        ("AUTONOMOUS_ENGINEERING_RUNS_PER_DAY", str(payload.runs_per_day)),
    )
    for name, value in updates:
        result = _run(
            [gh, "variable", "set", name, "--body", value, "--repo", _repository()],
            timeout=15,
        )
        if not result or result.returncode != 0:
            raise HTTPException(status_code=502, detail=f"ذخیرهٔ تنظیم {name} در GitHub ناموفق بود.")
    return {
        "saved": True,
        "model": model,
        "runs_per_day": payload.runs_per_day,
        "message": "تنظیمات عامل در متغیرهای مخزن GitHub ذخیره شد.",
    }


@router.post("/dispatch", status_code=202)
def dispatch_autonomous_engineering() -> dict[str, Any]:
    readiness = collect_status()
    if not readiness.get("github", {}).get("runs_available", False):
        raise HTTPException(status_code=503, detail="وضعیت اجراهای GitHub قابل دریافت نیست؛ اجرای جدید به‌صورت ایمن متوقف شد.")
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail="پیش‌نیازهای عامل کامل نیست؛ ابتدا وضعیت محلی را بررسی کنید.")
    active_runs = readiness.get("github", {}).get("recent_runs", [])
    if any(item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"} for item in active_runs):
        raise HTTPException(status_code=409, detail="یک اجرای عامل در حال انجام است؛ ابتدا وضعیت آن را بررسی کنید.")
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    result = _run(
        [gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main"],
        timeout=20,
    )
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="درخواست اجرای Workflow به GitHub ارسال نشد.")
    return {
        "accepted": True,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "message": "درخواست اجرا ارسال شد؛ برای تأیید شروع، وضعیت GitHub Actions را بازخوانی کنید.",
    }


@router.post("/cancel")
def cancel_autonomous_engineering() -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    result = _run(
        [
            gh, "run", "list", "--repo", _repository(), "--workflow", _WORKFLOW,
            "--limit", "10", "--json", "databaseId,status",
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
            and item.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}
        ),
        None,
    )
    if not active or not isinstance(active.get("databaseId"), int):
        raise HTTPException(status_code=409, detail="اجرای فعالی برای لغو وجود ندارد.")
    cancelled = _run(
        [gh, "run", "cancel", str(active["databaseId"]), "--repo", _repository()],
        timeout=12,
    )
    if not cancelled or cancelled.returncode != 0:
        raise HTTPException(status_code=502, detail="لغو اجرا توسط GitHub تأیید نشد.")
    return {"accepted": True, "run_id": active["databaseId"], "message": "درخواست لغو به GitHub ارسال شد."}
