"""Narrow GitHub CLI adapter for Autonomous Engineering run operations."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException


def authenticated_gh(*, run_command, which) -> str:
    gh = which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = run_command([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    return gh


def github_api_json(gh: str, path: str, *, timeout: int = 15, run_command, repository: str) -> dict[str, Any]:
    # Only the fixed engineering workflow and canonical numeric run endpoints
    # are supported. Rebuild the endpoint from constants and an integer rather
    # than forwarding request-derived text to the command line.
    if path == "actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100":
        endpoint = "actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100"
    else:
        match = re.fullmatch(r"actions/runs/([1-9][0-9]{0,19})(/jobs\?per_page=100)?", path)
        if match is None:
            raise HTTPException(status_code=422, detail="مسیر API اجرای GitHub معتبر نیست.")
        run_number = int(match.group(1))
        if match.group(2) is not None:
            endpoint = f"actions/runs/{run_number:d}/jobs?per_page=100"
        else:
            endpoint = f"actions/runs/{run_number:d}"
    result = run_command([gh, "api", f"repos/{repository}/{endpoint}"], timeout=timeout)
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="دریافت اطلاعات اجرا از GitHub ناموفق بود.")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="پاسخ GitHub برای اطلاعات اجرا معتبر نیست.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="ساختار پاسخ GitHub برای اطلاعات اجرا معتبر نیست.")
    return value
