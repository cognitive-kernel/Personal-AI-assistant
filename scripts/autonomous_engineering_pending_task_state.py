from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
OWNER_FILENAME = "pending-manual-task-owner.json"
_ALLOWED_SOURCES = {"manual_dispatch", "recovery", "legacy_migration"}


def pending_task_owner_path(state_directory: Path) -> Path:
    return state_directory / OWNER_FILENAME


def task_digest(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


def _validate_owner_record(record: Any, task: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RuntimeError("Pending task owner metadata must be a JSON object.")
    integer_fields = ("owner_attempt", "submitted_attempt", "claim_count")
    timestamp_fields = ("created_at", "claimed_at")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or any(not isinstance(record.get(key), str) or not record[key] for key in (
            "task_sha256", "owner_run_id", "submitted_run_id", "source"
        ))
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("task_sha256", "")))
        or record["task_sha256"] != task_digest(task)
        or any(
            isinstance(record.get(key), bool)
            or not isinstance(record.get(key), int)
            or record[key] < 1
            for key in integer_fields
        )
        or any(
            isinstance(record.get(key), bool)
            or not isinstance(record.get(key), (int, float))
            or not math.isfinite(float(record[key]))
            or record[key] <= 0
            for key in timestamp_fields
        )
        or record["source"] not in _ALLOWED_SOURCES
    ):
        raise RuntimeError(
            "Pending task owner metadata violates schema v1 or does not match the task; preserving it."
        )
    return record


def claim_pending_task_owner_unlocked(
    state_directory: Path,
    task: str,
    run_id: str,
    run_attempt: int,
    *,
    source: str,
) -> dict[str, Any]:
    """Claim a durable task while the caller holds the shared durable-state lock.

    The caller must first prove no other autonomous run is active. The task bytes
    are not changed; only the explicit execution owner/attempt lease is advanced.
    """
    if not isinstance(task, str) or not task.strip() or len(task) > 2000 or "\0" in task:
        raise ValueError("Pending manual task is empty or violates the 2,000-character/NUL contract.")
    if not isinstance(run_id, str) or not run_id.strip() or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", run_id):
        raise ValueError("A valid workflow run ID is required to claim a pending task.")
    if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt < 1:
        raise ValueError("A positive workflow run attempt is required to claim a pending task.")
    if source not in _ALLOWED_SOURCES:
        raise ValueError("Unsupported pending task ownership source.")

    state_directory.mkdir(parents=True, exist_ok=True)
    owner_path = pending_task_owner_path(state_directory)
    now = time.time()
    try:
        existing = json.loads(owner_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = None
    except (OSError, ValueError) as exc:
        raise RuntimeError("Pending task owner metadata is unreadable; preserving the task.") from exc

    if existing is None:
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "task_sha256": task_digest(task),
            "submitted_run_id": run_id,
            "submitted_attempt": run_attempt,
            "owner_run_id": run_id,
            "owner_attempt": run_attempt,
            "created_at": now,
            "claimed_at": now,
            "claim_count": 1,
            "source": source,
        }
    else:
        record = _validate_owner_record(existing, task)
        if record["owner_run_id"] != run_id or record["owner_attempt"] != run_attempt:
            record = {
                **record,
                "owner_run_id": run_id,
                "owner_attempt": run_attempt,
                "claimed_at": now,
                "claim_count": record["claim_count"] + 1,
            }

    _validate_owner_record(record, task)
    temporary = owner_path.with_name(owner_path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(owner_path)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def validate_pending_task_owner_unlocked(
    state_directory: Path,
    task: str,
    run_id: str,
    run_attempt: int,
) -> dict[str, Any]:
    """Validate that the current workflow run owns the exact persisted task."""
    owner_path = pending_task_owner_path(state_directory)
    try:
        record = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Pending task owner metadata is missing or unreadable.") from exc
    validated = _validate_owner_record(record, task)
    if validated["owner_run_id"] != run_id or validated["owner_attempt"] != run_attempt:
        raise RuntimeError("The current workflow run does not own the persisted manual task.")
    return validated
