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


def enqueue_learning_task(
    job_id: int,
    *,
    priority: int = 0,
    task_context: Any | None = None,
    authority_job_id: int | None = None,
) -> dict[str, Any]:
    """Enqueue/recover a learning job, optionally under a live learning task lease."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")
    if task_context is not None:
        if authority_job_id is None:
            raise ValueError("authority_job_id is required for task-authorized enqueue.")

    payload = {"job_id": job_id}
    base_key = f"learning-job:{job_id}"

    def create_authorized(dedupe_key: str) -> dict[str, Any]:
        with learning_connection() as conn:
            if task_context is not None:
                conn.execute("BEGIN IMMEDIATE")
                task_context.assert_current(
                    conn,
                    expected_task_type=LEARNING_TASK_TYPE,
                    expected_payload={"job_id": authority_job_id},
                )
            return task_manager.create_task(
                LEARNING_TASK_TYPE,
                payload,
                priority=priority,
                max_attempts=3,
                dedupe_key=dedupe_key,
                connection=conn,
            )

    existing = task_manager.get_task_by_dedupe_key(base_key)
    if existing is None:
        return create_authorized(base_key)
    if existing["status"] not in task_manager.TERMINAL_STATUSES:
        return existing

    try:
        job = learning.get_job(job_id)
    except KeyError:
        return existing
    if job["status"] in _TERMINAL_LEARNING_STATUSES:
        return existing
    if existing["status"] == "cancelled":
        return existing

    recovery_key = f"{base_key}:recovery:1"
    recovery = task_manager.get_task_by_dedupe_key(recovery_key)
    if recovery is None:
        return create_authorized(recovery_key)
    if recovery["status"] not in task_manager.TERMINAL_STATUSES:
        return recovery

    with learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if task_context is not None:
            task_context.assert_current(
                conn,
                expected_task_type=LEARNING_TASK_TYPE,
                expected_payload={"job_id": authority_job_id},
            )
        conn.execute(
            """UPDATE learning_jobs
               SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status IN ('queued', 'running', 'failed')""",
            ("Learning task recovery exhausted; human review is required.", job_id),
        )
    _record_learning_escalation(
        int(recovery["id"]),
        job_id,
        "attempts_exhausted",
        "The original task and its single recovery task are terminal while the learning job remains non-terminal.",
        task_context=task_context,
        authority_job_id=authority_job_id,
    )
    return recovery

def _record_learning_escalation(
    task_id: int,
    job_id: int,
    reason: str,
    error_summary: str = "",
    *,
    task_context: Any | None = None,
    authority_job_id: int | None = None,
) -> dict[str, Any]:
    """Create one durable human-review record under the originating task lease."""
    task = task_manager.get_task(task_id)
    if task is None:
        raise ValueError("Cannot escalate a missing learning task.")
    attempts = max(0, int(task.get("attempts", 0)))
    max_attempts = max(1, int(task.get("max_attempts", 1)))
    summary = str(error_summary or "")[:1000]
    with learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if task_context is not None:
            if authority_job_id is None:
                raise ValueError("authority_job_id is required for task-authorized escalation.")
            task_context.assert_current(
                conn,
                expected_task_type=LEARNING_TASK_TYPE,
                expected_payload={"job_id": authority_job_id},
            )
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
        context.assert_current(
            expected_task_type=LEARNING_TASK_TYPE, expected_payload={"job_id": job_id}
        )
        if status == "rejected":
            _record_learning_escalation(
                context.task_id, job_id, "quality_rejected", job.get("error") or "Lesson did not pass quality checks.",
                task_context=context, authority_job_id=job_id,
            )
        if status == "completed" and job.get("path_topic_id") is not None:
            learning.queue_next_researched_path_topic(
                int(job["path_topic_id"]), task_context=context, authority_job_id=job_id
            )
        return {"job_id": job_id, "learning_status": status, "already_terminal": True}

    # A task is reclaimed only after its previous lease expires. A stale learning
    # status must therefore be reset before this newly claimed attempt can resume it.
    if status in {"failed", "running"}:
        from . import learning_execution
        with learning_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            context.assert_current(
                conn,
                expected_task_type=LEARNING_TASK_TYPE,
                expected_payload={"job_id": job_id},
            )
            if status == "running" and learning_execution.has_live_queue_owner(conn, job_id):
                return {
                    "job_id": job_id,
                    "learning_status": "running",
                    "already_running": True,
                }
            conn.execute(
                """UPDATE learning_jobs
                   SET status='queued', execution_owner=NULL,
                       execution_task_id=NULL, execution_task_epoch=NULL,
                       error=NULL, updated_at=CURRENT_TIMESTAMP
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
    context.assert_current(
        expected_task_type=LEARNING_TASK_TYPE, expected_payload={"job_id": job_id}
    )
    try:
        learning.run_job(job_id, task_context=context)
    except learning.LearningError as exc:
        context.assert_current(
            expected_task_type=LEARNING_TASK_TYPE, expected_payload={"job_id": job_id}
        )
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

    context.assert_current(
        expected_task_type=LEARNING_TASK_TYPE, expected_payload={"job_id": job_id}
    )
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
    if status == "completed" and result.get("path_topic_id") is not None:
        learning.queue_next_researched_path_topic(int(result["path_topic_id"]))
    return {"job_id": job_id, "learning_status": status, "already_terminal": False}
