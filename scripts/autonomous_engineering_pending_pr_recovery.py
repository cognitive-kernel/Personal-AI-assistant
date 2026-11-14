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
    """Reconcile local PR metadata against GitHub while the caller holds the state lock.

    GitHub is authoritative for PR identity. Damaged or foreign-repository local
    records are quarantined, never silently deleted. Recovery only reconstructs
    metadata from a bot-owned PR in this repository with the controller's exact
    branch format. Multiple candidates always fail closed.
    """
    path = state_directory / "pending-autonomous-pr.json"
    invalid_local_state = False
    try:
        current = load_pending_pr_unlocked(state_directory)
    except RuntimeError:
        current = None
        invalid_local_state = path.exists()

    if current is not None and current.get("url") != (
        f"https://github.com/{repository}/pull/{current['number']}"
    ):
        current = None
        invalid_local_state = True

    candidates = [
        item for item in open_pull_requests
        if _is_controller_pr(item, repository)
    ]
    if len(candidates) > 1:
        raise RuntimeError(
            "Multiple open autonomous PRs exist; refusing to guess which task owns durable state."
        )

    if not invalid_local_state:
        if current is not None:
            if candidates and candidates[0].get("number") != current["number"]:
                raise RuntimeError(
                    "A different open autonomous PR exists beside the saved PR; preserving both records."
                )
            return current
        if candidates:
            recovered = _metadata_from_pr(candidates[0], repository)
            save_pending_pr_unlocked(recovered, repository, state_directory)
            return recovered
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Pending PR state cannot be inspected or quarantined safely.") from exc
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None

    authoritative: dict[str, Any] | None = None
    number = parsed.get("number") if isinstance(parsed, dict) else None
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        live = get_pull_request(number)
        if _is_controller_pr(live, repository):
            authoritative = live

    if authoritative is not None and candidates:
        if authoritative.get("number") != candidates[0].get("number"):
            raise RuntimeError(
                "A saved PR and a different open autonomous PR both exist; refusing ambiguous recovery."
            )
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
