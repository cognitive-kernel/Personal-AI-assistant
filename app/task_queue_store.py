"""Task creation, lookup, pagination, and durable event queries."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import db, task_manager_core as _core
from .task_manager_core import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    MAX_TASK_TYPE_CHARS,
    MAX_TASK_PAYLOAD_BYTES,
    MAX_TASK_RECORD_BYTES,
    MAX_TASK_PRIORITY,
    MAX_TASK_ATTEMPTS,
    MAX_TASK_DEDUPE_KEY_CHARS,
    TaskManagerError,
    TaskNotFoundError,
    _json,
    _task,
)

def _now():
    return _core._now()


def _timestamp(value=None):
    return _core._timestamp(value)


def initialize_task_store() -> None:
    """Ensure the learning database is at the registered schema version."""
    from .schema_registry import LEARNING_MIGRATIONS, initialize_database_file

    initialize_database_file(db.defaults.learning_db_path, LEARNING_MIGRATIONS)

def create_task(
    task_type: str,
    payload: dict[str, Any],
    *,
    priority: int = 0,
    max_attempts: int = 1,
    scheduled_at: datetime | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    if not isinstance(task_type, str) or not task_type.strip() or len(task_type.strip()) > MAX_TASK_TYPE_CHARS:
        raise ValueError(f"task_type must be a non-empty string of at most {MAX_TASK_TYPE_CHARS} characters.")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object.")
    if isinstance(priority, bool) or not isinstance(priority, int) or not -MAX_TASK_PRIORITY <= priority <= MAX_TASK_PRIORITY:
        raise ValueError(f"priority must be an integer between {-MAX_TASK_PRIORITY} and {MAX_TASK_PRIORITY}.")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= MAX_TASK_ATTEMPTS:
        raise ValueError(f"max_attempts must be an integer between 1 and {MAX_TASK_ATTEMPTS}.")
    if dedupe_key is not None and (
        not isinstance(dedupe_key, str) or not dedupe_key.strip()
        or len(dedupe_key.strip()) > MAX_TASK_DEDUPE_KEY_CHARS
    ):
        raise ValueError(f"dedupe_key must be a non-empty string of at most {MAX_TASK_DEDUPE_KEY_CHARS} characters when provided.")
    encoded_payload = _json(
        payload, max_bytes=MAX_TASK_PAYLOAD_BYTES, field_name="Task payload"
    )
    normalized_type = task_type.strip()
    normalized_key = dedupe_key.strip() if dedupe_key is not None else None
    due = _timestamp(scheduled_at)
    with db.learning_connection() as conn:
        try:
            cursor = conn.execute(
                """INSERT INTO task_queue(
                       task_type, payload_json, priority, max_attempts, scheduled_at, dedupe_key
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (normalized_type, encoded_payload, priority, max_attempts, due, normalized_key),
            )
            row = conn.execute(
                "SELECT * FROM task_queue WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        except sqlite3.IntegrityError:
            if normalized_key is None:
                raise
            row = conn.execute(
                "SELECT * FROM task_queue WHERE dedupe_key = ?", (normalized_key,)
            ).fetchone()
            if row is None:
                raise
            if (
                row["task_type"] != normalized_type
                or row["payload_json"] != encoded_payload
                or row["max_attempts"] != max_attempts
            ):
                raise ValueError(
                    "dedupe_key already exists for a different task definition."
                )
    return _task(row)  # type: ignore[return-value]

def get_task(task_id: int) -> dict[str, Any] | None:
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
    return _task(row)

def get_task_by_dedupe_key(dedupe_key: str) -> dict[str, Any] | None:
    if not isinstance(dedupe_key, str) or not dedupe_key.strip():
        raise ValueError("dedupe_key must be a non-empty string.")
    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT * FROM task_queue WHERE dedupe_key = ?", (dedupe_key.strip(),)
        ).fetchone()
    return _task(row)

def list_tasks(*, statuses: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000.")
    if statuses is not None:
        allowed = {
            "queued", "running", "pause_requested", "paused", "cancel_requested",
            "cancelled", "succeeded", "failed",
        }
        if not statuses or any(status not in allowed for status in statuses):
            raise ValueError("statuses contains an unsupported status.")
        placeholders = ",".join("?" for _ in statuses)
        query = f"SELECT * FROM task_queue WHERE status IN ({placeholders}) ORDER BY priority DESC, id LIMIT ?"
        params: tuple[Any, ...] = (*statuses, limit)
    else:
        query = "SELECT * FROM task_queue ORDER BY priority DESC, id LIMIT ?"
        params = (limit,)
    with db.learning_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_task(row) for row in rows]  # type: ignore[misc]

def _encode_task_cursor(priority: int, task_id: int) -> str:
    payload = json.dumps({"v": 1, "p": priority, "i": task_id}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

def _decode_task_cursor(cursor: str | None) -> tuple[int, int] | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > 256:
        raise ValueError("cursor is invalid.")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid.") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "p", "i"}
        or isinstance(payload.get("v"), bool)
        or not isinstance(payload.get("v"), int)
        or payload.get("v") != 1
        or isinstance(payload.get("p"), bool)
        or not isinstance(payload.get("p"), int)
        or isinstance(payload.get("i"), bool)
        or not isinstance(payload.get("i"), int)
        or payload["i"] < 1
    ):
        raise ValueError("cursor is invalid.")
    return payload["p"], payload["i"]

def list_tasks_page(
    *, statuses: list[str] | None = None, limit: int = 50, cursor: str | None = None
) -> dict[str, Any]:
    """Return a bounded keyset-paginated task page in stable priority/id order."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    allowed = {
        "queued", "running", "pause_requested", "paused", "cancel_requested",
        "cancelled", "succeeded", "failed",
    }
    if statuses is not None and (not statuses or any(status not in allowed for status in statuses)):
        raise ValueError("statuses contains an unsupported status.")
    decoded = _decode_task_cursor(cursor)
    clauses: list[str] = []
    params: list[Any] = []
    if statuses is not None:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if decoded is not None:
        priority, task_id = decoded
        clauses.append("(priority < ? OR (priority = ? AND id > ?))")
        params.extend((priority, priority, task_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit + 1)
    with db.learning_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM task_queue{where} ORDER BY priority DESC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
    has_more = len(rows) > limit
    selected = rows[:limit]
    tasks = [_task(row) for row in selected]
    next_cursor = None
    if has_more and selected:
        last = selected[-1]
        next_cursor = _encode_task_cursor(int(last["priority"]), int(last["id"]))
    return {"tasks": tasks, "next_cursor": next_cursor, "has_more": has_more}

def list_task_events(
    task_id: int, *, limit: int = 100, before_id: int | None = None
) -> dict[str, Any]:
    """List durable task lifecycle events without exposing task payloads."""
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
        raise ValueError("task_id must be a positive integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    if before_id is not None and (
        isinstance(before_id, bool) or not isinstance(before_id, int) or before_id < 1
    ):
        raise ValueError("before_id must be a positive integer.")
    params: list[Any] = [task_id]
    where = "task_id = ?"
    if before_id is not None:
        where += " AND id < ?"
        params.append(before_id)
    params.append(limit + 1)
    with db.learning_connection() as conn:
        rows = conn.execute(
            f"""SELECT id, task_id, event_type, old_status, new_status,
                       old_priority, new_priority, old_scheduled_at, new_scheduled_at,
                       occurred_at
                FROM task_events WHERE {where} ORDER BY id DESC LIMIT ?""",
            tuple(params),
        ).fetchall()
    has_more = len(rows) > limit
    events = [dict(row) for row in rows[:limit]]
    next_before_id = events[-1]["id"] if has_more and events else None
    return {"events": events, "has_more": has_more, "next_before_id": next_before_id}

def list_terminal_task_ids(
    *, limit: int = 1000, min_terminal_age_seconds: int = 60
) -> list[int]:
    """Return settled task IDs only after a grace period since terminal transition."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000.")
    if (
        isinstance(min_terminal_age_seconds, bool)
        or not isinstance(min_terminal_age_seconds, int)
        or min_terminal_age_seconds < 0
    ):
        raise ValueError("min_terminal_age_seconds must be a non-negative integer.")
    cutoff = _timestamp(_now() - timedelta(seconds=min_terminal_age_seconds))
    with db.learning_connection() as conn:
        rows = conn.execute(
            """SELECT id FROM task_queue
               WHERE status IN ('succeeded', 'failed', 'cancelled') AND updated_at <= ?
               ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
    return [int(row["id"]) for row in rows]
