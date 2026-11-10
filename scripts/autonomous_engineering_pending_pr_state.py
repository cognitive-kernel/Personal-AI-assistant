from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from .autonomous_engineering_state_lock import durable_state_lock
    from .autonomous_engineering_state_paths import execution_state_directory
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_state_lock import durable_state_lock
    from autonomous_engineering_state_paths import execution_state_directory


def autonomous_state_directory() -> Path:
    """Return the shared local state root for durable task and PR records."""
    return execution_state_directory()


def pending_pr_path() -> Path:
    return autonomous_state_directory() / "pending-autonomous-pr.json"


def save_pending_pr_unlocked(value: dict[str, Any], repo: str) -> None:
    """Validate and atomically replace the pending PR record; caller owns the state lock."""
    string_fields = ("url", "head_sha", "base_sha", "branch", "run_id")
    if (
        not isinstance(value, dict)
        or isinstance(value.get("number"), bool)
        or not isinstance(value.get("number"), int)
        or value["number"] < 1
        or any(not isinstance(value.get(key), str) or not value[key] for key in string_fields)
        or isinstance(value.get("task"), bool)
        or not isinstance(value.get("task"), int)
        or value["task"] < 1
        or not re.fullmatch(r"[0-9a-f]{40}", value["head_sha"])
        or not re.fullmatch(r"[0-9a-f]{40}", value["base_sha"])
        or not value["branch"].startswith("codex/local-engineering-")
        or value["url"] != f"https://github.com/{repo}/pull/{value['number']}"
    ):
        raise RuntimeError("Refusing to persist invalid pending PR metadata.")
    path = pending_pr_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_pending_pr(value: dict[str, Any], repo: str) -> None:
    with durable_state_lock(autonomous_state_directory()):
        save_pending_pr_unlocked(value, repo)


def load_pending_pr_unlocked() -> dict[str, Any] | None:
    path = pending_pr_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("Pending PR state is unreadable; preserving it and refusing a new task.") from exc
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Pending PR state is unreadable; preserving it and refusing a new task.") from exc
    required = ("number", "url", "head_sha", "base_sha", "branch", "run_id", "task")
    if (
        not isinstance(value, dict)
        or isinstance(value.get("number"), bool)
        or not isinstance(value.get("number"), int)
        or value["number"] < 1
        or any(not isinstance(value.get(key), str) or not value[key] for key in required if key not in {"number", "task"})
        or isinstance(value.get("task"), bool)
        or not isinstance(value.get("task"), int)
        or value["task"] < 1
        or not re.fullmatch(r"[0-9a-f]{40}", str(value.get("head_sha", "")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(value.get("base_sha", "")))
        or not str(value.get("branch", "")).startswith("codex/local-engineering-")
        or not re.fullmatch(r"https://github\\.com/[^/]+/[^/]+/pull/[1-9][0-9]*", str(value.get("url", "")))
    ):
        raise RuntimeError("Pending PR state violates its durable contract; preserving it and refusing a new task.")
    return value


def load_pending_pr() -> dict[str, Any] | None:
    with durable_state_lock(autonomous_state_directory()):
        return load_pending_pr_unlocked()


def clear_pending_pr() -> None:
    with durable_state_lock(autonomous_state_directory()):
        pending_pr_path().unlink(missing_ok=True)


def clear_pending_task() -> None:
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        (state_dir / "pending-manual-task.txt").unlink(missing_ok=True)
        (state_dir / "pending-manual-task-owner.json").unlink(missing_ok=True)
        (state_dir / "pending-manual-task-owner.json").unlink(missing_ok=True)


def clear_pending_state() -> None:
    """Clear the accepted PR and its submitted task as one durable-state transaction."""
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        (state_dir / "pending-autonomous-pr.json").unlink(missing_ok=True)
        (state_dir / "pending-manual-task.txt").unlink(missing_ok=True)
