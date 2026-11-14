"""Persistent escalation records for learning failures requiring user action."""

from __future__ import annotations

import json
from typing import Any

from . import task_manager
from .db import learning_connection


def _record_learning_escalation(
    task_id: int,
    job_id: int,
    reason: str,
    error_summary: str = "",
) -> dict[str, Any]:
    """Create one durable human-review record per terminal learning task."""
    task = task_manager.get_task(task_id)
    if task is None:
        raise ValueError("Cannot escalate a missing learning task.")
    attempts = max(0, int(task.get("attempts", 0)))
    max_attempts = max(1, int(task.get("max_attempts", 1)))
    summary = str(error_summary or "")[:1000]
    with learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_escalations(
                   task_id, job_id, reason, attempts, max_attempts, error_summary
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO NOTHING""",
            (task_id, job_id, reason, attempts, max_attempts, summary),
        )
        row = conn.execute(
            "SELECT * FROM learning_escalations WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return dict(row)


def list_learning_escalations(
    status: str = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List durable human-review items, prioritizing task priority and recency."""
    if status not in {"pending", "reviewed", "dismissed"}:
        raise ValueError("Unsupported escalation status.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200.")
    with learning_connection() as conn:
        rows = conn.execute(
            """SELECT e.id, e.task_id, e.job_id, e.reason, e.status, e.attempts,
                      e.max_attempts, e.error_summary, e.review_note, e.created_at,
                      e.updated_at, e.reviewed_at, t.priority, t.status AS task_status,
                      j.topic, j.status AS learning_status
               FROM learning_escalations e
               JOIN task_queue t ON t.id=e.task_id
               JOIN learning_jobs j ON j.id=e.job_id
               WHERE e.status=?
               ORDER BY t.priority DESC, e.created_at DESC, e.id DESC
               LIMIT ?""",
            (status, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_learning_escalation(
    escalation_id: int,
    status: str,
    review_note: str = "",
) -> dict[str, Any]:
    """Resolve a pending escalation without altering the underlying lesson or evidence."""
    if isinstance(escalation_id, bool) or not isinstance(escalation_id, int) or escalation_id < 1:
        raise ValueError("escalation_id must be a positive integer.")
    if status not in {"reviewed", "dismissed"}:
        raise ValueError("Escalation status must be reviewed or dismissed.")
    if not isinstance(review_note, str) or len(review_note) > 2000:
        raise ValueError("Review note must be a string of at most 2000 characters.")
    with learning_connection() as conn:
        row = conn.execute(
            "SELECT * FROM learning_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(escalation_id)
        if row["status"] != "pending":
            if row["status"] == status and row["review_note"] == review_note.strip():
                return dict(row)
            raise ValueError("Escalation has already been resolved.")
        conn.execute(
            """UPDATE learning_escalations
               SET status=?, review_note=?, updated_at=CURRENT_TIMESTAMP,
                   reviewed_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='pending'""",
            (status, review_note.strip(), escalation_id),
        )
        updated = conn.execute(
            "SELECT * FROM learning_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
    return dict(updated)
