"""Allow-listed task adapter for durable learning jobs.

The adapter translates queue retries into learning-job recovery and returns the
actual learning outcome instead of treating a swallowed application error as success.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import learning
from .db import learning_connection
from . import task_manager
from . import ollama
from .task_worker import PermanentTaskError, RetryableTaskError, TaskContext

logger = logging.getLogger(__name__)

LEARNING_TASK_TYPE = "learning.run_job"
_TERMINAL_LEARNING_STATUSES = {"completed", "rejected", "cancelled"}


def enqueue_learning_task(job_id: int, *, priority: int = 0) -> dict[str, Any]:
    """Enqueue or boundedly recover a learning job without discarding queue history."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")

    payload = {"job_id": job_id}
    base_key = f"learning-job:{job_id}"
    existing = task_manager.get_task_by_dedupe_key(base_key)
    if existing is None:
        return task_manager.create_task(
            LEARNING_TASK_TYPE, payload, priority=priority, max_attempts=3,
            dedupe_key=base_key,
        )
    if existing["status"] not in task_manager.TERMINAL_STATUSES:
        return existing

    try:
        job = learning.get_job(job_id)
    except KeyError:
        return existing
    if job["status"] in _TERMINAL_LEARNING_STATUSES:
        return existing
    # Explicit cancellation must not be undone by startup reconciliation.
    if existing["status"] == "cancelled":
        return existing

    # A terminal queue record can coexist with a non-terminal learning record if
    # the worker died before the domain handler got a chance to settle it. Preserve
    # the original task as history and allow exactly one durable recovery task.
    recovery_key = f"{base_key}:recovery:1"
    recovery = task_manager.get_task_by_dedupe_key(recovery_key)
    if recovery is None:
        return task_manager.create_task(
            LEARNING_TASK_TYPE, payload, priority=priority, max_attempts=3,
            dedupe_key=recovery_key,
        )
    if recovery["status"] not in task_manager.TERMINAL_STATUSES:
        return recovery

    # Never create an unbounded chain of recovery tasks. Once the one recovery
    # task is terminal but the learning record is still non-terminal, fail visibly
    # and leave an auditable human-review item instead of looping on every restart.
    with learning_connection() as conn:
        conn.execute(
            """UPDATE learning_jobs
               SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status IN ('queued', 'running', 'failed')""",
            ("Learning task recovery exhausted; human review is required.", job_id),
        )
    _record_learning_escalation(
        int(recovery["id"]), job_id, "attempts_exhausted",
        "The original task and its single recovery task are terminal while the learning job remains non-terminal.",
    )
    return recovery


def reconcile_queued_learning_tasks() -> int:
    """Repair each queued/running learning record independently.

    A single malformed record or transient enqueue failure must not starve later
    jobs or prevent application startup. Failed records are logged and retried
    by the next startup/maintenance reconciliation cycle.
    """
    with learning_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM learning_jobs WHERE status IN ('queued', 'running') ORDER BY id"
        ).fetchall()
    reconciled = 0
    for row in rows:
        job_id = int(row["id"])
        try:
            enqueue_learning_task(job_id)
        except Exception:
            logger.exception(
                "Could not reconcile durable learning task for job %s; it will be retried.",
                job_id,
            )
            continue
        reconciled += 1
    return reconciled


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


def cancel_learning_task(job_id: int) -> dict[str, Any] | None:
    """Cancel whichever durable queue task currently owns this learning job."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")

    base_key = f"learning-job:{job_id}"
    candidates = [
        task_manager.get_task_by_dedupe_key(base_key),
        task_manager.get_task_by_dedupe_key(f"{base_key}:recovery:1"),
    ]
    active = [
        task for task in candidates
        if task is not None and task["status"] not in task_manager.TERMINAL_STATUSES
    ]
    # A recovery task may be active after the original task has failed. Cancelling
    # only the historical base task would leave the recovery worker running.
    if active:
        return task_manager.request_cancel(int(active[-1]["id"]))
    return candidates[0] or candidates[1]


def cancel_learning_job(job_id: int) -> dict[str, Any] | None:
    """Synchronize generic task-API cancellation with the learning-domain record."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")
    task = task_manager.get_task_by_dedupe_key(f"learning-job:{job_id}")
    if task is None:
        return None

    try:
        job = learning.get_job(job_id)
    except KeyError:
        # The task can still be cancelled safely if its domain row was removed.
        return cancel_learning_task(job_id)

    if job["status"] not in _TERMINAL_LEARNING_STATUSES:
        with learning_connection() as conn:
            conn.execute(
                """UPDATE learning_jobs
                   SET status='cancelled', error='لغو از API مدیریت وظایف',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status IN ('queued', 'running', 'failed')""",
                (job_id,),
            )
    return cancel_learning_task(job_id)


def handle_learning_job(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    """Run one persisted learning job under the task queue's lease and controls."""
    job_id = payload.get("job_id")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise PermanentTaskError("learning task payload requires a positive integer job_id")

    try:
        job = learning.get_job(job_id)
    except KeyError as exc:
        raise PermanentTaskError(f"Learning job {job_id} does not exist") from exc

    status = job["status"]
    if status in _TERMINAL_LEARNING_STATUSES:
        if status == "rejected":
            _record_learning_escalation(
                context.task_id, job_id, "quality_rejected", job.get("error") or "Lesson did not pass quality checks."
            )
        return {"job_id": job_id, "learning_status": status, "already_terminal": True}

    # A task is reclaimed only after its previous lease expires. A stale learning
    # status must therefore be reset before this newly claimed attempt can resume it.
    if status in {"failed", "running"}:
        context.control_status()  # Verify this worker still owns a live task lease.
        with learning_connection() as conn:
            conn.execute(
                """UPDATE learning_jobs
                   SET status='queued', error=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status IN ('failed', 'running')""",
                (job_id,),
            )
        job = learning.get_job(job_id)
        status = job["status"]

    if status != "queued":
        raise PermanentTaskError(
            f"Learning job {job_id} cannot run from status {status!r}"
        )

    context.checkpoint({"phase": "learning_queued", "job_id": job_id})
    try:
        learning.run_job(job_id)
    except learning.LearningError as exc:
        cause = exc.__cause__
        task = task_manager.get_task(context.task_id) or {}
        attempts = int(task.get("attempts", 0))
        max_attempts = int(task.get("max_attempts", 1))
        if isinstance(cause, ollama.OllamaError):
            if cause.retryable:
                if attempts >= max_attempts:
                    _record_learning_escalation(
                        context.task_id, job_id, "attempts_exhausted",
                        f"Model request failed after {attempts} attempt(s): {type(cause).__name__}",
                    )
                raise RetryableTaskError("Learning model request failed; retry may succeed.") from exc
            _record_learning_escalation(
                context.task_id, job_id, "permanent_failure",
                f"Non-retryable model error: {type(cause).__name__}",
            )
            raise PermanentTaskError("Learning model configuration or policy is invalid.") from exc
        if isinstance(cause, (ValueError, TypeError, KeyError, AssertionError, sqlite3.IntegrityError)):
            _record_learning_escalation(
                context.task_id, job_id, "permanent_failure",
                f"Non-retryable learning error: {type(cause).__name__}",
            )
            raise PermanentTaskError("Learning job failed because of invalid input or an internal invariant.") from exc
        if attempts >= max_attempts:
            _record_learning_escalation(
                context.task_id, job_id, "attempts_exhausted",
                f"Learning execution failed after {attempts} attempt(s): {type(cause).__name__ if cause else type(exc).__name__}",
            )
        raise RetryableTaskError("Learning job execution failed; retry may succeed.") from exc

    result = learning.get_job(job_id)
    status = result["status"]
    if status == "rejected":
        _record_learning_escalation(
            context.task_id, job_id, "quality_rejected",
            result.get("error") or "Lesson did not pass quality checks.",
        )
    if status not in _TERMINAL_LEARNING_STATUSES:
        raise learning.LearningError(
            f"Learning job {job_id} returned without a terminal result (status={status!r})"
        )
    return {"job_id": job_id, "learning_status": status, "already_terminal": False}
