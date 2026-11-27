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
        user_value = item.get("user", {})
        head_value = item.get("head", {})
        base_value = item.get("base", {})
        if not all(isinstance(value, dict) for value in (user_value, head_value, base_value)):
            raise HTTPException(
                status_code=502,
                detail="ساختار جزئیات PRهای GitHub معتبر نیست؛ وظیفهٔ جدید ارسال نشد.",
            )
        user = user_value
        head = head_value
        base = base_value
        head_repo_value = head.get("repo", {})
        if not isinstance(head_repo_value, dict):
            raise HTTPException(
                status_code=502,
                detail="ساختار مخزن مبدأ PR در پاسخ GitHub معتبر نیست؛ وظیفهٔ جدید ارسال نشد.",
            )
        head_repo = head_repo_value
        branch = head.get("ref")
        if branch is not None and not isinstance(branch, str):
            raise HTTPException(
                status_code=502,
                detail="نام شاخهٔ مبدأ PR در پاسخ GitHub معتبر نیست؛ وظیفهٔ جدید ارسال نشد.",
            )
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
