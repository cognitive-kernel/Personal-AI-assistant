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
from pydantic import BaseModel, Field, StrictInt, StrictStr

from .task_routes import require_loopback_client
from .autonomous_engineering_progress import get_progress, storage_accessible
from .ollama_context import MIN_CODEX_CONTEXT_TOKENS, OllamaModelMetadataError, maximum_context_tokens, supports_tool_calling

_RUN_PROGRESS_CACHE: dict[str, Any] = {"run_id": None, "at": 0.0, "value": None}

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


def _ollama_status(model: str) -> dict[str, Any]:
    empty = {
        "online": False,
        "model": model,
        "model_installed": False,
        "model_selectable": False,
        "model_is_codex_context_variant": _is_codex_context_variant(model),
        "models": [],
        "selectable_models": [],
        "model_context_length": None,
        "model_capabilities": [],
        "model_supports_tools": False,
        "model_metadata_available": False,
        "model_context_supported": False,
        "model_context_error": f"Codex requires at least {MIN_CODEX_CONTEXT_TOKENS} context tokens.",
        "model_metadata_error": "Ollama metadata is unavailable.",
    }
    # Local Ollama metadata must never be sent through an environment proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/tags",
        headers={"Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=3) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Ollama /api/tags response exceeded the safety limit.")
        payload = json.loads(body.decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = sorted({
            str(item.get("name", ""))
            for item in models
            if isinstance(item, dict) and item.get("name")
        })
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        return empty

    installed = bool(model) and model in names
    is_context_variant = _is_codex_context_variant(model)
    selectable_names = [name for name in names if not _is_codex_context_variant(name)]
    result = {
        **empty,
        "online": True,
        "model_installed": installed,
        "model_selectable": installed and not is_context_variant,
        "model_is_codex_context_variant": is_context_variant,
        "models": names,
        "selectable_models": selectable_names,
        "model_metadata_error": (
            "The saved model is an internal Codex context variant; select its original Ollama model."
            if is_context_variant else
            None if installed else
            "No Ollama model is saved in repository settings." if not model else
            "The saved model is not installed locally."
        ),
    }
    if not installed or is_context_variant:
        return result

    show_request = urllib.request.Request(
        "http://127.0.0.1:11434/api/show",
        data=json.dumps({"model": model, "verbose": True}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(show_request, timeout=5) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Ollama /api/show response exceeded the safety limit.")
        details_payload = json.loads(body.decode("utf-8"))
        if not isinstance(details_payload, dict):
            raise ValueError("Ollama /api/show returned an invalid response.")
        details = details_payload.get("details") if isinstance(details_payload.get("details"), dict) else {}
        model_info = details_payload.get("model_info") if isinstance(details_payload.get("model_info"), dict) else {}
        architecture = details.get("family")
        try:
            context_length = maximum_context_tokens(model_info, architecture)
        except OllamaModelMetadataError:
            context_length = None
        raw_capabilities = details_payload.get("capabilities")
        capabilities = sorted({
            value for value in raw_capabilities
            if isinstance(value, str) and value
        }) if isinstance(raw_capabilities, list) else []
        metadata_available = context_length is not None
        context_supported = metadata_available and context_length >= MIN_CODEX_CONTEXT_TOKENS
        result.update({
            "model_context_length": context_length,
            "model_capabilities": capabilities,
            "model_supports_tools": supports_tool_calling(capabilities),
            "model_metadata_available": metadata_available,
            "model_context_supported": context_supported,
            "model_context_error": (
                None if context_supported else
                f"Selected model context is below the Codex minimum of {MIN_CODEX_CONTEXT_TOKENS} tokens."
                if metadata_available else
                "Selected model context metadata is unavailable."
            ),
            "model_metadata_error": (
                None if metadata_available else
                "Ollama did not publish a valid architecture-specific context_length."
            ),
        })
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        result["model_metadata_error"] = f"Could not verify selected-model metadata: {type(exc).__name__}"
    return result

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
    settings = {"model": "", "interval_hours": _DEFAULT_INTERVAL_HOURS, "execution_mode": "continuous", "paused": False}
    if authenticated:
        result = _run(
            [
                gh, "run", "list", "--repo", _repository(), "--workflow", _WORKFLOW,
                "--limit", "30", "--json",
                "databaseId,status,conclusion,createdAt,url,headBranch",
            ],
            timeout=12,
        )
        if result and result.returncode == 0:
            try:
                parsed = json.loads(result.stdout)
                if isinstance(parsed, list):
                    mapped_runs = [
                        {
                            "id": item.get("databaseId"),
                            "status": item.get("status"),
                            "conclusion": item.get("conclusion"),
                            "created_at": item.get("createdAt"),
                            "url": item.get("url"),
                            "branch": item.get("headBranch"),
                        }
                        for item in parsed
                        if isinstance(item, dict)
                    ]
                    recent_runs = mapped_runs[:5]
                    visible_ids = {item["id"] for item in recent_runs}
                    active_states = {"queued", "in_progress", "waiting", "requested", "pending"}
                    recent_runs.extend(
                        item for item in mapped_runs[5:]
                        if item.get("status") in active_states and item.get("id") not in visible_ids
                    )
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
                    candidate_interval = configured.get("AUTONOMOUS_ENGINEERING_INTERVAL_HOURS", "")
                    candidate_mode = configured.get("AUTONOMOUS_ENGINEERING_MODE", "continuous")
                    settings["paused"] = configured.get("AUTONOMOUS_ENGINEERING_PAUSED", "false").lower() == "true"
                    if _MODEL_PATTERN.fullmatch(candidate_model):
                        settings["model"] = candidate_model
                    if candidate_interval.isdigit() and int(candidate_interval) in _ALLOWED_INTERVAL_HOURS:
                        settings["interval_hours"] = int(candidate_interval)
                    if candidate_mode in {"scheduled", "continuous"}:
                        settings["execution_mode"] = candidate_mode
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
    model = settings.get("model", "")
    interval_hours = settings.get("interval_hours", _DEFAULT_INTERVAL_HOURS)
    execution_mode = settings.get("execution_mode", "continuous")
    paused = bool(settings.get("paused", False))
    ollama = _ollama_status(model)
    codex = _codex_status()
    runner = _runner_status()
    progress_storage = storage_accessible()
    checks = {
        "ollama_online": ollama["online"],
        "model_installed": ollama["model_installed"],
        "model_selectable": ollama.get("model_selectable", ollama["model_installed"] and not _is_codex_context_variant(model)) is True,
        "model_is_codex_context_variant": ollama.get("model_is_codex_context_variant") is True,
        "model_context_metadata_available": ollama.get("model_metadata_available") is True,
        "model_context_supported": ollama.get("model_context_supported") is True,
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
        if key not in {"codex_installed", "model_is_codex_context_variant"}
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
        "model_context_supported": ollama.get("model_context_supported", False),
        "model_context_error": ollama.get("model_context_error"),
        "model_capabilities": ollama.get("model_capabilities", []),
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


@router.get("/progress")
def autonomous_engineering_progress(
    period: str = "daily", start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    result = get_progress(period=period, start_date=start_date, end_date=end_date)
    result["current"] = _enrich_progress_from_github(result.get("current"))
    return result


def _save_repository_variables(gh: str, updates: tuple[tuple[str, str], ...]) -> None:
    """Apply repository-variable updates as a rollback-protected transaction."""
    listing = _run(
        [gh, "variable", "list", "--repo", _repository(), "--json", "name,value"],
        timeout=12,
    )
    if not listing or listing.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail="Could not snapshot existing GitHub repository settings; no settings were changed.",
        )
    try:
        current_values = json.loads(listing.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub returned invalid repository-variable state; no settings were changed.",
        ) from exc
    if not isinstance(current_values, list):
        raise HTTPException(
            status_code=502,
            detail="GitHub returned an invalid repository-variable list; no settings were changed.",
        )
    previous = {
        str(item.get("name")): str(item.get("value", ""))
        for item in current_values
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    applied: list[str] = []
    for name, value in updates:
        result = _run(
            [gh, "variable", "set", name, "--body", value, "--repo", _repository()],
            timeout=15,
        )
        if result and result.returncode == 0:
            applied.append(name)
            continue

        rollback_errors: list[str] = []
        # Include the failed write itself: a client timeout/non-zero exit can
        # occur after GitHub accepted the update, so its remote state is uncertain.
        for changed_name in reversed([*applied, name]):
            if changed_name in previous:
                rollback = _run(
                    [gh, "variable", "set", changed_name, "--body", previous[changed_name], "--repo", _repository()],
                    timeout=15,
                )
            else:
                rollback = _run(
                    [gh, "variable", "delete", changed_name, "--repo", _repository()],
                    timeout=15,
                )
            if not rollback or rollback.returncode != 0:
                verification = _run(
                    [gh, "variable", "list", "--repo", _repository(), "--json", "name,value"],
                    timeout=12,
                )
                try:
                    observed = json.loads(verification.stdout) if verification and verification.returncode == 0 else None
                except (TypeError, ValueError):
                    observed = None
                observed_values = {
                    str(item.get("name")): str(item.get("value", ""))
                    for item in observed
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                } if isinstance(observed, list) else None
                restored = (
                    observed_values is not None
                    and (
                        observed_values.get(changed_name) == previous[changed_name]
                        if changed_name in previous
                        else changed_name not in observed_values
                    )
                )
                if not restored:
                    rollback_errors.append(changed_name)
        if rollback_errors:
            detail = (
                f"Saving {name} failed and rollback could not restore: "
                + ", ".join(rollback_errors)
                + ". Inspect the repository Actions variables before retrying."
            )
        else:
            detail = f"Saving {name} failed; all earlier changes were rolled back."
        raise HTTPException(status_code=502, detail=detail)


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
        maximum = local_model.get("model_context_length")
        raise HTTPException(
            status_code=422,
            detail=f"Codex به حداقل {MIN_CODEX_CONTEXT_TOKENS} توکن Context نیاز دارد؛ سقف مدل انتخاب‌شده {maximum} است و ذخیره نشد.",
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
