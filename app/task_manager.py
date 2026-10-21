"""Persistent SQLite-backed task queue with leases and cooperative controls.

This module is deliberately independent of FastAPI and learning workers. Call
initialize_task_store() after app.db.init_databases() before using the queue.
"""
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

ACTIVE_STATUSES = {"running", "pause_requested", "cancel_requested"}
TERMINAL_STATUSES = {"cancelled", "succeeded", "failed"}
RETRY_BASE_DELAY_SECONDS = 2
RETRY_MAX_DELAY_SECONDS = 300


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


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Task data must be JSON-serializable and contain no NaN/Infinity.") from exc


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
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task_type must be a non-empty string.")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object.")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError("priority must be an integer.")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be an integer greater than zero.")
    if dedupe_key is not None and (
        not isinstance(dedupe_key, str) or not dedupe_key.strip()
    ):
        raise ValueError("dedupe_key must be a non-empty string when provided.")
    encoded_payload = _json(payload)
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


def claim_next_task(
    worker_id: str, *, lease_seconds: int = 300, task_types: list[str] | tuple[str, ...] | None = None
) -> dict[str, Any] | None:
    """Atomically claim one due task; expired running leases may be reclaimed."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string.")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer.")
    if task_types is not None and (
        not isinstance(task_types, (list, tuple)) or not task_types
        or any(not isinstance(item, str) or not item.strip() for item in task_types)
    ):
        raise ValueError("task_types must be a non-empty list of non-empty strings.")
    normalized_types = tuple(dict.fromkeys(item.strip() for item in task_types)) if task_types is not None else None
    now = _now()
    now_text = _timestamp(now)
    expires = _timestamp(now + timedelta(seconds=lease_seconds))
    with db.learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Recover workers that disappeared during a cooperative pause/cancel request.
        conn.execute(
            """UPDATE task_queue SET status='cancelled', lease_owner=NULL, lease_expires_at=NULL,
               updated_at=? WHERE status='cancel_requested' AND lease_expires_at <= ?""",
            (now_text, now_text),
        )
        conn.execute(
            """UPDATE task_queue SET status='paused', lease_owner=NULL, lease_expires_at=NULL,
               updated_at=? WHERE status='pause_requested' AND lease_expires_at <= ?""",
            (now_text, now_text),
        )
        # Exhausted tasks become terminal instead of being reclaimed forever.
        conn.execute(
            """UPDATE task_queue SET status='failed', error=COALESCE(error, 'Worker lease expired; retry limit reached.'),
               lease_owner=NULL, lease_expires_at=NULL, updated_at=?
               WHERE status='running' AND lease_expires_at <= ? AND attempts >= max_attempts""",
            (now_text, now_text),
        )
        type_clause = ""
        query_params: tuple[Any, ...] = (now_text, now_text)
        if normalized_types is not None:
            placeholders = ",".join("?" for _ in normalized_types)
            type_clause = f" AND task_type IN ({placeholders})"
            query_params = (*query_params, *normalized_types)
        row = conn.execute(
            """SELECT id FROM task_queue
               WHERE ((status='queued' AND scheduled_at <= ?)
                  OR (status='running' AND lease_expires_at <= ? AND attempts < max_attempts))"""
            + type_clause +
            " ORDER BY priority DESC, scheduled_at ASC, id ASC LIMIT 1",
            query_params,
        ).fetchone()
        if row is None:
            return None
        task_id = int(row["id"])
        conn.execute(
            """UPDATE task_queue SET status='running', attempts=attempts+1,
               lease_owner=?, lease_expires_at=?, error=NULL, updated_at=? WHERE id=?""",
            (worker_id.strip(), expires, now_text, task_id),
        )
        claimed = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(claimed)


def _owned_running_task(conn: sqlite3.Connection, task_id: int, worker_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFoundError(f"Task {task_id} does not exist.")
    if row["lease_owner"] != worker_id or row["status"] not in ACTIVE_STATUSES:
        raise TaskManagerError("Task is not owned by this worker or is no longer active.")
    if row["lease_expires_at"] is None or row["lease_expires_at"] <= _timestamp():
        raise TaskManagerError("Worker lease has expired; the task must be reclaimed before mutation.")
    return row


def renew_lease(task_id: int, worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any]:
    """Extend a worker lease while the task remains cooperatively active."""
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer.")
    expires = _timestamp(_now() + timedelta(seconds=lease_seconds))
    now = _timestamp()
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
        if row["status"] != "running":
            raise TaskManagerError(f"Cannot renew a task with status {row['status']}.")
        conn.execute(
            "UPDATE task_queue SET lease_expires_at=?, updated_at=? WHERE id=?",
            (expires, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def save_checkpoint(task_id: int, worker_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
    encoded = _json(checkpoint)
    now = _timestamp()
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
        if row["status"] == "cancel_requested":
            conn.execute(
                """UPDATE task_queue SET status='cancelled', checkpoint_json=?, lease_owner=NULL,
                   lease_expires_at=NULL, updated_at=? WHERE id=?""", (encoded, now, task_id)
            )
        elif row["status"] == "pause_requested":
            conn.execute(
                """UPDATE task_queue SET status='paused', checkpoint_json=?, lease_owner=NULL,
                   lease_expires_at=NULL, updated_at=? WHERE id=?""", (encoded, now, task_id)
            )
        else:
            conn.execute(
                "UPDATE task_queue SET checkpoint_json=?, updated_at=? WHERE id=?",
                (encoded, now, task_id),
            )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def complete_task(task_id: int, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
    encoded = _json(result)
    now = _timestamp()
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
        if row["status"] != "running":
            raise TaskManagerError(f"Cannot complete a task with status {row['status']}.")
        conn.execute(
            """UPDATE task_queue SET status='succeeded', result_json=?, error=NULL,
               lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
            (encoded, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def fail_task(
    task_id: int, worker_id: str, error: str, *, retry: bool = True
) -> dict[str, Any]:
    """Record failure and schedule bounded exponential backoff for retryable attempts."""
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be a non-empty string.")
    now_dt = _now()
    now = _timestamp(now_dt)
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
        should_retry = retry and row["attempts"] < row["max_attempts"]
        if should_retry:
            # The attempt that just failed determines a 2s, 4s, 8s... delay, capped at 5m.
            delay = min(
                RETRY_MAX_DELAY_SECONDS,
                RETRY_BASE_DELAY_SECONDS * (2 ** min(max(int(row["attempts"]) - 1, 0), 8)),
            )
            scheduled_at = _timestamp(now_dt + timedelta(seconds=delay))
            status = "queued"
        else:
            scheduled_at = row["scheduled_at"]
            status = "failed"
        conn.execute(
            """UPDATE task_queue SET status=?, error=?, scheduled_at=?,
               lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
            (status, error[:4000], scheduled_at, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def request_cancel(task_id: int) -> dict[str, Any]:
    now = _timestamp()
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        status = row["status"]
        if status in TERMINAL_STATUSES:
            return _task(row)  # type: ignore[return-value]
        next_status = "cancel_requested" if status in ACTIVE_STATUSES else "cancelled"
        if status == "paused":
            next_status = "cancelled"
        conn.execute(
            """UPDATE task_queue SET status=?, lease_owner=CASE WHEN ?='cancelled' THEN NULL ELSE lease_owner END,
               lease_expires_at=CASE WHEN ?='cancelled' THEN NULL ELSE lease_expires_at END, updated_at=?
               WHERE id=?""",
            (next_status, next_status, next_status, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def request_pause(task_id: int) -> dict[str, Any]:
    now = _timestamp()
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        status = row["status"]
        if status in TERMINAL_STATUSES:
            return _task(row)  # type: ignore[return-value]
        if status == "queued":
            next_status = "paused"
        elif status == "running":
            next_status = "pause_requested"
        else:
            return _task(row)  # type: ignore[return-value]
        conn.execute("UPDATE task_queue SET status=?, updated_at=? WHERE id=?", (next_status, now, task_id))
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def resume_task(task_id: int, *, scheduled_at: datetime | None = None) -> dict[str, Any]:
    now = _timestamp()
    due = _timestamp(scheduled_at) if scheduled_at is not None else None
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        if row["status"] != "paused":
            raise TaskManagerError("Only paused tasks can be resumed.")
        if due is None:
            conn.execute(
                "UPDATE task_queue SET status='queued', updated_at=? WHERE id=?",
                (now, task_id),
            )
        else:
            conn.execute(
                "UPDATE task_queue SET status='queued', scheduled_at=?, updated_at=? WHERE id=?",
                (due, now, task_id),
            )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def get_worker_control(task_id: int, worker_id: str) -> str:
    """Return running/pause_requested/cancel_requested for cooperative worker polling."""
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
    return str(row["status"])


def update_task_priority(task_id: int, priority: int) -> dict[str, Any]:
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError("priority must be an integer.")
    now = _timestamp()
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        if row["status"] not in {"queued", "paused"}:
            raise TaskManagerError("Priority can only be changed for queued or paused tasks.")
        conn.execute(
            "UPDATE task_queue SET priority=?, updated_at=? WHERE id=?",
            (priority, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]


def reschedule_task(task_id: int, scheduled_at: datetime) -> dict[str, Any]:
    due = _timestamp(scheduled_at)
    now = _timestamp()
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        if row["status"] not in {"queued", "paused"}:
            raise TaskManagerError("Only queued or paused tasks can be rescheduled.")
        conn.execute(
            "UPDATE task_queue SET scheduled_at=?, updated_at=? WHERE id=?",
            (due, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]
