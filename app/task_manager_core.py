"""Shared data contracts and serialization helpers for the durable task queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

ACTIVE_STATUSES = {"running", "pause_requested", "cancel_requested"}
TERMINAL_STATUSES = {"cancelled", "succeeded", "failed"}
RETRY_BASE_DELAY_SECONDS = 2
RETRY_MAX_DELAY_SECONDS = 300
MAX_TASK_TYPE_CHARS = 100
MAX_TASK_PAYLOAD_BYTES = 1024 * 1024
MAX_TASK_RECORD_BYTES = 4 * 1024 * 1024
MAX_TASK_PRIORITY = 100_000
MAX_TASK_ATTEMPTS = 20
MAX_TASK_DEDUPE_KEY_CHARS = 512


class TaskManagerError(RuntimeError):
    """Raised when a task operation violates the task state/ownership contract."""


class TaskNotFoundError(TaskManagerError):
    """Raised when an operation targets a missing task."""


def _now() -> datetime:
    return datetime.now(timezone.utc)

def _timestamp(value: datetime | None = None) -> str:
    current = value or _now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")
    return current.astimezone(timezone.utc).isoformat(timespec="microseconds")

def _json(
    value: Any,
    *,
    max_bytes: int | None = None,
    field_name: str = "Task data",
) -> str:
    if max_bytes is None:
        max_bytes = MAX_TASK_RECORD_BYTES
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(
            f"{field_name} must be JSON-serializable UTF-8 and contain no NaN/Infinity."
        ) from exc
    if size > max_bytes:
        raise ValueError(f"{field_name} exceeds the {max_bytes}-byte serialized limit.")
    return encoded

def _task(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    item["checkpoint"] = (
        json.loads(item.pop("checkpoint_json"))
        if item.get("checkpoint_json") is not None
        else None
    )
    result_json = item.pop("result_json")
    item["result"] = json.loads(result_json) if result_json is not None else None
    return item
