"""Atomic task claiming, lease fencing, checkpointing, and completion."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import db, task_manager_core as _core
from .task_manager_core import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    MAX_TASK_RECORD_BYTES,
    TaskManagerError,
    TaskNotFoundError,
    _json,
    _task,
)

def _now():
    return _core._now()


def _timestamp(value=None):
    return _core._timestamp(value)


def claim_next_task(
    worker_id: str, *, lease_seconds: int = 300, task_types: list[str] | tuple[str, ...] | None = None,
    exclude_task_types: list[str] | tuple[str, ...] | None = None
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
    if exclude_task_types is not None and (
        not isinstance(exclude_task_types, (list, tuple)) or not exclude_task_types
        or any(not isinstance(item, str) or not item.strip() for item in exclude_task_types)
    ):
        raise ValueError("exclude_task_types must be a non-empty list of non-empty strings.")
    normalized_types = tuple(dict.fromkeys(item.strip() for item in task_types)) if task_types is not None else None
    normalized_excluded = tuple(dict.fromkeys(item.strip() for item in exclude_task_types)) if exclude_task_types is not None else None
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
            type_clause += f" AND task_type IN ({placeholders})"
            query_params = (*query_params, *normalized_types)
        if normalized_excluded is not None:
            placeholders = ",".join("?" for _ in normalized_excluded)
            type_clause += f" AND task_type NOT IN ({placeholders})"
            query_params = (*query_params, *normalized_excluded)
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
               lease_owner=?, lease_expires_at=?, lease_epoch=lease_epoch+1,
               error=NULL, updated_at=? WHERE id=?""",
            (worker_id.strip(), expires, now_text, task_id),
        )
        claimed = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(claimed)

def _owned_running_task(
    conn: sqlite3.Connection, task_id: int, worker_id: str, lease_epoch: int
) -> sqlite3.Row:
    if isinstance(lease_epoch, bool) or not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise ValueError("lease_epoch must be a positive integer.")
    row = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFoundError(f"Task {task_id} does not exist.")
    if int(row["lease_epoch"]) != lease_epoch:
        raise TaskManagerError("Worker lease epoch is stale; task ownership was reclaimed.")
    if row["lease_owner"] != worker_id or row["status"] not in ACTIVE_STATUSES:
        raise TaskManagerError("Task is not owned by this worker or is no longer active.")
    if row["lease_expires_at"] is None or row["lease_expires_at"] <= _timestamp():
        raise TaskManagerError("Worker lease has expired; the task must be reclaimed before mutation.")
    return row

def assert_task_owner(
    conn: sqlite3.Connection, task_id: int, worker_id: str, lease_epoch: int
) -> sqlite3.Row:
    """Validate a task lease and fencing epoch inside the caller's transaction."""
    return _owned_running_task(conn, task_id, worker_id, lease_epoch)

def renew_lease(
    task_id: int, worker_id: str, *, lease_epoch: int, lease_seconds: int = 300
) -> dict[str, Any]:
    """Extend a worker lease while the task remains cooperatively active."""
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer.")
    expires = _timestamp(_now() + timedelta(seconds=lease_seconds))
    now = _timestamp()
    with db.learning_connection() as conn:
        # Serialize ownership validation with lease reclamation by other workers.
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_running_task(conn, task_id, worker_id, lease_epoch)
        if row["status"] != "running":
            raise TaskManagerError(f"Cannot renew a task with status {row['status']}.")
        conn.execute(
            "UPDATE task_queue SET lease_expires_at=?, updated_at=? WHERE id=?",
            (expires, now, task_id),
        )
        updated = conn.execute("SELECT * FROM task_queue WHERE id=?", (task_id,)).fetchone()
    return _task(updated)  # type: ignore[return-value]

def save_checkpoint(
    task_id: int, worker_id: str, checkpoint: dict[str, Any], *, lease_epoch: int
) -> dict[str, Any]:
    encoded = _json(checkpoint, field_name="Task checkpoint")
    now = _timestamp()
    with db.learning_connection() as conn:
        # Hold the write reservation across the ownership check and checkpoint write.
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_running_task(conn, task_id, worker_id, lease_epoch)
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

def complete_task(
    task_id: int, worker_id: str, result: dict[str, Any], *, lease_epoch: int
) -> dict[str, Any]:
    encoded = _json(result, field_name="Task result")
    now = _timestamp()
    with db.learning_connection() as conn:
        # A reclaiming worker must not interleave between validation and completion.
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_running_task(conn, task_id, worker_id, lease_epoch)
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
    task_id: int, worker_id: str, error: str, *, lease_epoch: int, retry: bool = True
) -> dict[str, Any]:
    """Honor pending pause/cancel controls before scheduling bounded retry backoff."""
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be a non-empty string.")
    now_dt = _now()
    now = _timestamp(now_dt)
    with db.learning_connection() as conn:
        # Serialize retry scheduling with any competing lease reclamation.
        conn.execute("BEGIN IMMEDIATE")
        row = _owned_running_task(conn, task_id, worker_id, lease_epoch)
        # Explicit user control takes precedence over automatic retry. This also
        # covers failures before a handler reaches its next cooperative checkpoint.
        if row["status"] == "cancel_requested":
            scheduled_at = row["scheduled_at"]
            status = "cancelled"
        elif row["status"] == "pause_requested":
            scheduled_at = row["scheduled_at"]
            status = "paused"
        else:
            should_retry = retry and row["attempts"] < row["max_attempts"]
            if should_retry:
                # The attempt that just failed determines a 2s, 4s, 8s... delay, capped at 5m.
                exponential_delay = min(
                    RETRY_MAX_DELAY_SECONDS,
                    RETRY_BASE_DELAY_SECONDS * (2 ** min(max(int(row["attempts"]) - 1, 0), 8)),
                )
                # Add bounded jitter so many failed jobs do not retry in lockstep.
                # The maximum remains a hard cap even when jitter selects its upper bound.
                jittered_delay = random.uniform(
                    exponential_delay * 0.8,
                    exponential_delay * 1.2,
                )
                delay = min(RETRY_MAX_DELAY_SECONDS, jittered_delay)
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
