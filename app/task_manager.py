"""Persistent SQLite-backed task queue with leases and cooperative controls.

This module is deliberately independent of FastAPI and learning workers. Call
initialize_task_store() after app.db.init_databases() before using the queue.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

ACTIVE_STATUSES = {"running", "pause_requested", "cancel_requested"}
TERMINAL_STATUSES = {"cancelled", "succeeded", "failed"}


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
    """Create the version-1 task schema idempotently in the learning database."""
    with db.learning_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL CHECK(length(trim(task_type)) > 0),
                dedupe_key TEXT,
                payload_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','pause_requested','paused',
                                     'cancel_requested','cancelled','succeeded','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK(max_attempts >= 1),
                checkpoint_json TEXT,
                result_json TEXT,
                error TEXT,
                scheduled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                lease_owner TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_ready "
            "ON task_queue(status, scheduled_at, priority DESC, id)"
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_queue)").fetchall()}
        if "dedupe_key" not in columns:
            conn.execute("ALTER TABLE task_queue ADD COLUMN dedupe_key TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_queue_dedupe "
            "ON task_queue(dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_lease "
            "ON task_queue(status, lease_expires_at)"
        )


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
        not isinstance(dedupe_key, str) or not dedupe_key.strip() or len(dedupe_key) > 255
    ):
        raise ValueError("dedupe_key must be a non-empty string of at most 255 characters.")
    dedupe_key = dedupe_key.strip() if dedupe_key is not None else None
    due = _timestamp(scheduled_at)
    with db.learning_connection() as conn:
        if dedupe_key is not None:
            existing = conn.execute(
                "SELECT * FROM task_queue WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone()
            if existing is not None:
                if existing["task_type"] != task_type.strip() or existing["payload_json"] != _json(payload):
                    raise ValueError("dedupe_key is already used by a different task definition.")
                return _task(existing)  # type: ignore[return-value]
        try:
            cursor = conn.execute(
                """INSERT INTO task_queue(
                       task_type, dedupe_key, payload_json, priority, max_attempts, scheduled_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (task_type.strip(), dedupe_key, _json(payload), priority, max_attempts, due),
            )
            row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            # A concurrent submitter may have won the unique-key race.
            if dedupe_key is None:
                raise
            row = conn.execute(
                "SELECT * FROM task_queue WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone()
            if row is None:
                raise
            if row["task_type"] != task_type.strip() or row["payload_json"] != _json(payload):
                raise ValueError("dedupe_key is already used by a different task definition.")
    return _task(row)  # type: ignore[return-value]


def get_task(task_id: int) -> dict[str, Any] | None:
    with db.learning_connection() as conn:
        row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
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


def claim_next_task(worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any] | None:
    """Atomically claim one due task; expired running leases may be reclaimed."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string.")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer.")
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
        row = conn.execute(
            """SELECT id FROM task_queue
               WHERE (status='queued' AND scheduled_at <= ?)
                  OR (status='running' AND lease_expires_at <= ? AND attempts < max_attempts)
               ORDER BY priority DESC, scheduled_at ASC, id ASC LIMIT 1""",
            (now_text, now_text),
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
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be a non-empty string.")
    now = _timestamp()
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id)
        status = "queued" if retry and row["attempts"] < row["max_attempts"] else "failed"
        conn.execute(
            """UPDATE task_queue SET status=?, error=?, lease_owner=NULL, lease_expires_at=NULL,
               updated_at=? WHERE id=?""",
            (status, error[:4000], now, task_id),
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
