from __future__ import annotations

import json
import math
import os
import re
import time
from urllib.parse import urlsplit
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


def _cleanup_marker_path(state_dir: Path) -> Path:
    return state_dir / "pending-state-cleanup.json"


def _write_cleanup_marker_unlocked(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = _cleanup_marker_path(state_dir)
    record = {
        "schema_version": 1,
        "operation": "clear_after_verified_post_merge",
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "created_at": time.time(),
    }
    temporary = marker.with_name(marker.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)


def recover_pending_state_cleanup_unlocked(state_dir: Path) -> bool:
    """Resume an accepted post-merge cleanup interrupted by process termination."""
    marker = _cleanup_marker_path(state_dir)
    try:
        raw = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError("Pending-state cleanup marker is unreadable; preserving all state.") from exc
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Pending-state cleanup marker is malformed; preserving all state.") from exc
    if (
        not isinstance(record, dict)
        or isinstance(record.get("schema_version"), bool)
        or record.get("schema_version") != 1
        or record.get("operation") != "clear_after_verified_post_merge"
        or not isinstance(record.get("run_id"), str)
        or not record["run_id"]
        or isinstance(record.get("created_at"), bool)
        or not isinstance(record.get("created_at"), (int, float))
        or not math.isfinite(float(record["created_at"]))
        or record["created_at"] <= 0
    ):
        raise RuntimeError("Pending-state cleanup marker violates schema v1; preserving all state.")
    for filename in (
        "pending-autonomous-pr.json",
        "pending-manual-task.txt",
        "pending-manual-task-owner.json",
    ):
        (state_dir / filename).unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return True


def recover_pending_state_cleanup() -> bool:
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        return recover_pending_state_cleanup_unlocked(state_dir)


def save_pending_pr(value: dict[str, Any], repo: str) -> None:
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        recover_pending_state_cleanup_unlocked(state_dir)
        save_pending_pr_unlocked(value, repo)


def _valid_pending_pr_url(url: Any, number: int) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(
            r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*",
            parsed.path,
        ) is not None
        and parsed.path.endswith(f"/pull/{number}")
    )


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
        or not _valid_pending_pr_url(value.get("url"), value["number"])
    ):
        raise RuntimeError("Pending PR state violates its durable contract; preserving it and refusing a new task.")
    return value


def load_pending_pr() -> dict[str, Any] | None:
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        recover_pending_state_cleanup_unlocked(state_dir)
        return load_pending_pr_unlocked()


def clear_pending_pr() -> None:
    with durable_state_lock(autonomous_state_directory()):
        pending_pr_path().unlink(missing_ok=True)


def clear_pending_task() -> None:
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        (state_dir / "pending-manual-task.txt").unlink(missing_ok=True)
        (state_dir / "pending-manual-task-owner.json").unlink(missing_ok=True)


def clear_pending_state() -> None:
    """Commit an idempotent cleanup intent, then clear accepted task/PR state."""
    state_dir = autonomous_state_directory()
    with durable_state_lock(state_dir):
        recover_pending_state_cleanup_unlocked(state_dir)
        _write_cleanup_marker_unlocked(state_dir)
        recover_pending_state_cleanup_unlocked(state_dir)
