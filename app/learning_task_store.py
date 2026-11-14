"""Durable learning-task scheduling, reconciliation, and cancellation."""

from __future__ import annotations

import logging
from typing import Any

from . import learning, task_manager
from .db import learning_connection
from .learning_escalations import _record_learning_escalation
from .task_ownership import LEARNING_JOB_TASK_TYPE

logger = logging.getLogger(__name__)
LEARNING_TASK_TYPE = LEARNING_JOB_TASK_TYPE
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
