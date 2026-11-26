"""Fail-closed GitHub preflight for controller-owned pull requests."""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import HTTPException


def has_open_autonomous_pr(
    gh: str,
    *,
    repository: str,
    run_command: Callable[..., Any],
) -> bool:
    """Return whether the controller has an open PR; fail closed on unknown state."""
    result = run_command(
        [gh, "api", f"repos/{repository}/pulls?state=open&per_page=100"],
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
            and head_repo.get("full_name") == repository
            and isinstance(branch, str)
            and branch.startswith("codex/local-engineering-")
            and base.get("ref") == "main"
            and item.get("state", "open") == "open"
        ):
            return True
    return False
