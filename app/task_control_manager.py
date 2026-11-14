"""User-facing task controls and scheduling operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import db, task_manager_core as _core
from .task_manager_core import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    TaskManagerError,
    TaskNotFoundError,
    _task,
)
from .task_lease_manager import _owned_running_task

def _timestamp(value=None):
    return _core._timestamp(value)


def delete_task(task_id: int) -> dict[str, Any]:
    """Delete a task only when no worker can still be executing it."""
    now = _timestamp()
    with db.learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task {task_id} does not exist.")
        if row["status"] in ACTIVE_STATUSES:
            raise TaskManagerError(
                "Active tasks must be cancelled and reach a non-running state before deletion."
            )
        if row["status"] not in TERMINAL_STATUSES | {"queued", "paused"}:
            raise TaskManagerError(f"Tasks with status {row['status']} cannot be deleted.")
        deleted = {
            "id": int(row["id"]),
            "task_type": str(row["task_type"]),
            "status": str(row["status"]),
            "deleted_at": now,
        }
        conn.execute("DELETE FROM task_queue WHERE id=?", (task_id,))
    return deleted

def request_cancel(task_id: int) -> dict[str, Any]:
    now = _timestamp()
    with db.learning_connection() as conn:
        # Serialize the status read with worker claims and cooperative settlement.
        conn.execute("BEGIN IMMEDIATE")
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
        # Prevent a queued task from being claimed between status validation and pause.
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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

def get_worker_control(task_id: int, worker_id: str, *, lease_epoch: int) -> str:
    """Return running/pause_requested/cancel_requested for cooperative worker polling."""
    with db.learning_connection() as conn:
        row = _owned_running_task(conn, task_id, worker_id, lease_epoch)
    return str(row["status"])

def update_task_priority(task_id: int, priority: int) -> dict[str, Any]:
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError("priority must be an integer.")
    now = _timestamp()
    with db.learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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
