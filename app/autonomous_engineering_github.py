"""Narrow GitHub CLI adapter for Autonomous Engineering run operations."""

from __future__ import annotations

import json
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
    result = run_command([gh, "api", f"repos/{repository}/{path}"], timeout=timeout)
    if not result or result.returncode != 0:
        raise HTTPException(status_code=502, detail="دریافت اطلاعات اجرا از GitHub ناموفق بود.")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="پاسخ GitHub برای اطلاعات اجرا معتبر نیست.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="ساختار پاسخ GitHub برای اطلاعات اجرا معتبر نیست.")
    return value
