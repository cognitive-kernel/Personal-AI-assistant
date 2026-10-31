"""Local diagnostics and explicit controls for the Ollama-backed GitHub engineering agent."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException

from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/autonomous-engineering",
    tags=["autonomous-engineering"],
    dependencies=[Depends(require_loopback_client)],
)

_DEFAULT_REPOSITORY = "cognitive-kernel/Personal-AI-assistant"
_WORKFLOW = "autonomous-codex-engineering.yml"
_DEFAULT_MODEL = "gpt-oss:20b"


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
        with urllib.request.urlopen(request, timeout=2) as response:
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
        return {"cli_installed": False, "authenticated": False, "recent_runs": []}
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    authenticated = bool(auth and auth.returncode == 0)
    recent_runs: list[dict[str, Any]] = []
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
            except ValueError:
                recent_runs = []
    return {
        "cli_installed": True,
        "authenticated": authenticated,
        "recent_runs": recent_runs,
    }


def collect_status() -> dict[str, Any]:
    model = _model()
    ollama = _ollama_status(model)
    codex = _codex_status()
    runner = _runner_status()
    github = _github_snapshot()
    checks = {
        "ollama_online": ollama["online"],
        "model_installed": ollama["model_installed"],
        "codex_installed": codex["installed"],
        "runner_running": runner["running"],
        "github_cli_authenticated": github["authenticated"],
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "ollama": ollama,
        "codex": codex,
        "runner": runner,
        "github": github,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "model": model,
        "platform": platform.platform(),
        "local_only_inference": True,
    }


@router.get("/status")
def autonomous_engineering_status() -> dict[str, Any]:
    return collect_status()


@router.post("/dispatch", status_code=202)
def dispatch_autonomous_engineering() -> dict[str, Any]:
    readiness = collect_status()
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
