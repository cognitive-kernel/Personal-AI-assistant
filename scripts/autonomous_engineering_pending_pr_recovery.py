from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

try:
    from .autonomous_engineering_pending_pr_state import (
        load_pending_pr_unlocked,
        save_pending_pr_unlocked,
    )
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_pending_pr_state import (
        load_pending_pr_unlocked,
        save_pending_pr_unlocked,
    )


_BRANCH = re.compile(
    r"^codex/local-engineering-(?P<run_id>[0-9]+)-(?P<attempt>[0-9]+)-task-(?P<task>[0-9]+)$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _is_controller_pr(value: Any, repository: str) -> bool:
    if not isinstance(value, dict):
        return False
    head = value.get("head") or {}
    base = value.get("base") or {}
    user = value.get("user") or {}
    head_repo = head.get("repo") or {}
    branch = head.get("ref")
    return (
        user.get("login") == "github-actions[bot]"
        and head_repo.get("full_name") == repository
        and base.get("ref") == "main"
        and isinstance(branch, str)
        and _BRANCH.fullmatch(branch) is not None
    )


def _metadata_from_pr(value: dict[str, Any], repository: str) -> dict[str, Any]:
    if not _is_controller_pr(value, repository):
        raise RuntimeError("GitHub PR is not an autonomous controller PR for this repository.")
    branch = value["head"]["ref"]
    match = _BRANCH.fullmatch(branch)
    number = value.get("number")
    head_sha = (value.get("head") or {}).get("sha")
    base_sha = (value.get("base") or {}).get("sha")
    url = value.get("html_url")
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
        or not isinstance(head_sha, str)
        or not _SHA.fullmatch(head_sha)
        or not isinstance(base_sha, str)
        or not _SHA.fullmatch(base_sha)
        or url != f"https://github.com/{repository}/pull/{number}"
    ):
        raise RuntimeError("GitHub PR metadata is incomplete; preserving local pending state.")
    return {
        "number": number,
        "url": url,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": branch,
        "run_id": match.group("run_id"),
        "task": int(match.group("task")),
    }


def reconcile_pending_pr_state_unlocked(
    state_directory: Path,
    repository: str,
    open_pull_requests: list[dict[str, Any]],
    get_pull_request: Callable[[int], dict[str, Any]],
) -> dict[str, Any] | None:
    """Reconcile a damaged local PR record against GitHub's authoritative PR data.

    Caller must hold the shared durable-state lock. Unknown or stale local
    metadata is quarantined, never silently deleted. A real controller PR is
    reconstructed only from its verified GitHub identity and exact branch form.
    """
    path = state_directory / "pending-autonomous-pr.json"
    try:
        current = load_pending_pr_unlocked(state_directory)
        if current is not None and current.get("url") != (
            f"https://github.com/{repository}/pull/{current['number']}"
        ):
            raise RuntimeError("Pending PR metadata points to a different repository.")
    except RuntimeError:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("Pending PR state cannot be inspected or quarantined safely.") from exc
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None

        candidates = [
            item for item in open_pull_requests
            if _is_controller_pr(item, repository)
        ]
        if len(candidates) > 1:
            raise RuntimeError(
                "Multiple open autonomous PRs exist; refusing to guess which task owns durable state."
            )

        authoritative: dict[str, Any] | None = None
        number = parsed.get("number") if isinstance(parsed, dict) else None
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            live = get_pull_request(number)
            if _is_controller_pr(live, repository):
                authoritative = live
        if authoritative is None and candidates:
            authoritative = candidates[0]

        if authoritative is not None:
            recovered = _metadata_from_pr(authoritative, repository)
            save_pending_pr_unlocked(recovered, repository, state_directory)
            return recovered

        quarantine = path.with_name(
            f"{path.name}.quarantine-{os.getpid()}-{time.time_ns()}.json"
        )
        try:
            path.replace(quarantine)
        except OSError as exc:
            raise RuntimeError(
                "Stale pending PR metadata could not be quarantined; preserving all task state."
            ) from exc
        return None

    return current
