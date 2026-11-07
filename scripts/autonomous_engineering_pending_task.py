from __future__ import annotations

from typing import Any


def _is_open_autonomous_pr(item: Any, repository: str) -> bool:
    if not isinstance(item, dict):
        return False
    head = item.get("head") or {}
    base = item.get("base") or {}
    author = (item.get("user") or {}).get("login")
    head_repo = (head.get("repo") or {}).get("full_name")
    branch = head.get("ref")
    return (
        author == "github-actions[bot]"
        and head_repo == repository
        and isinstance(branch, str)
        and branch.startswith("codex/local-engineering-")
        and base.get("ref") == "main"
        and item.get("state", "open") == "open"
    )


def validate_manual_task_submission(
    *,
    manual_task: str,
    pending_task: str,
    open_pull_requests: list[dict[str, Any]],
    repository: str,
) -> None:
    """Prevent a new manual request from replacing work that is not yet accepted."""
    if not manual_task:
        return
    if any(_is_open_autonomous_pr(item, repository) for item in open_pull_requests):
        raise ValueError(
            "A previous autonomous task PR is still open. Its exact task must be recovered "
            "and accepted before another manual task can be queued."
        )
    if pending_task and pending_task != manual_task:
        raise ValueError(
            "Another manual task is still pending. It cannot be replaced before its verified merge."
        )
