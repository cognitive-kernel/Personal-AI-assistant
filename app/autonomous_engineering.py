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
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt, StrictStr

from .task_routes import require_loopback_client
from .autonomous_engineering_progress import get_progress, pending_work_status, storage_accessible
from .ollama_context import maximum_context_tokens, supports_tool_calling
from .autonomous_engineering_state import durable_state_lock, execution_state_directory
from .autonomous_engineering_model_pull import get_model_pull, start_model_pull

_RUN_PROGRESS_CACHE: dict[str, Any] = {"run_id": None, "at": 0.0, "value": None}
_STATUS_PROBE_CONTEXT = threading.local()

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


class ModelPullRequest(BaseModel):
    model: StrictStr = Field(min_length=1, max_length=120)


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
        maximum = maximum_context_tokens(model_info, details.get("family"))
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


def collect_status() -> dict[str, Any]:
    # These checks perform independent local/network I/O. Run them concurrently:
    # serial execution adds the GitHub CLI timeouts to several Ollama HTTP calls
    # (including the hardware/context estimate), which can exceed the UI's
    # 40-second request deadline even when every individual call is bounded.
    github_settings: dict[str, Any] = {}

    def _status_ollama() -> dict[str, Any]:
        # The probe policy is thread-local, so set it in the worker that performs
        # the Ollama check rather than in the request thread.
        _STATUS_PROBE_CONTEXT.allow_live_tool_probe = False
        try:
            return _ollama_status(str(github_settings.get("model", "")))
        finally:
            del _STATUS_PROBE_CONTEXT.allow_live_tool_probe

    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="autonomous-status") as pool:
        github_future = pool.submit(_github_snapshot)
        codex_future = pool.submit(_codex_status)
        runner_future = pool.submit(_runner_status)
        storage_future = pool.submit(storage_accessible)
        # Resolve GitHub first because its repository variables select the model.
        # The Ollama check starts immediately with the saved model only after that
        # value is known; all remaining checks are already running concurrently.
        github = github_future.result()
        github_settings = github.get("settings", {})
        model = github_settings.get("model", "")
        ollama_future = pool.submit(_status_ollama)
        codex = codex_future.result()
        runner = runner_future.result()
        progress_storage = storage_future.result()
        ollama = ollama_future.result()

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
        "platform": platform.platform(),
        "local_only_inference": True,
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
        path = execution_state_directory() / "run-diagnostics" / f"{run_id}.json"
        if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_RUN_DIAGNOSTIC_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
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
        detail = (result.stderr or result.stdout or "")[-1000:] if result else ""
        raise HTTPException(status_code=502, detail="حذف اجرا توسط GitHub تأیید نشد. " + detail)
    return {"deleted": True, "run_id": trusted_run_id, "message": "اجرای پایان‌یافته از تاریخچهٔ GitHub حذف شد."}


@router.post("/cleanup")
def cleanup_autonomous_engineering_temporary_data() -> dict[str, Any]:
    gh = _authenticated_gh()
    runs = _workflow_runs(gh)
    if any(item.get("status") in _ACTIVE_RUN_STATES for item in runs):
        raise HTTPException(
            status_code=409,
            detail="عامل هنوز اجرای فعال یا در صف دارد؛ پاک‌سازی داده‌های موقت تا پایان اجرا مجاز نیست.",
        )
    directory = execution_state_directory()
    marker_names = (
        "pending-manual-task.txt",
        "pending-manual-task-owner.json",
        "pending-autonomous-pr.json",
        "pending-state-cleanup.json",
    )
    removed = 0
    removed_bytes = 0
    failed_files: list[str] = []
    try:
        if directory.is_dir():
            with durable_state_lock(directory, timeout_seconds=2):
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

    # These three names are the controller's declared worktree runtime files;
    # durable checkpoints and all pending task/PR ownership files are excluded.
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

    return {
        "cleaned": not failed_files,
        "files_removed": removed,
        "bytes_removed": removed_bytes,
        "failed_files": failed_files,
        "preserved": ["history.jsonl", "scorecard-baseline.json", "checkpoint files", "pending task/PR markers"],
        "message": (
            "پاک‌سازی کامل شد؛ تاریخچه، checkpointها و وضعیت پایدار عامل حفظ شدند."
            if not failed_files
            else "پاک‌سازی ناقص بود؛ بعضی فایل‌های موقت به‌دلیل خطای دسترسی حذف نشدند."
        ),
    }
