"""Allow-listed task adapter for durable learning jobs.

The adapter translates queue retries into learning-job recovery and returns the
actual learning outcome instead of treating a swallowed application error as success.
"""
from __future__ import annotations

from typing import Any

from . import learning
from .db import learning_connection
from . import task_manager
from .task_worker import TaskContext

LEARNING_TASK_TYPE = "learning.run_job"
_TERMINAL_LEARNING_STATUSES = {"completed", "rejected", "cancelled"}


def enqueue_learning_task(job_id: int, *, priority: int = 0) -> dict[str, Any]:
    """Enqueue one learning job idempotently; the durable queue stores no source text."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")
    return task_manager.create_task(
        LEARNING_TASK_TYPE,
        {"job_id": job_id},
        priority=priority,
        max_attempts=3,
        dedupe_key=f"learning-job:{job_id}",
    )


def reconcile_queued_learning_tasks() -> int:
    """Ensure queued/running learning records have a durable task after a restart."""
    with learning_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM learning_jobs WHERE status IN ('queued', 'running') ORDER BY id"
        ).fetchall()
    for row in rows:
        enqueue_learning_task(int(row["id"]))
    return len(rows)


def handle_learning_job(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    """Run one persisted learning job under the task queue's lease and controls."""
    job_id = payload.get("job_id")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("learning task payload requires a positive integer job_id")

    try:
        job = learning.get_job(job_id)
    except KeyError as exc:
        raise ValueError(f"Learning job {job_id} does not exist") from exc

    status = job["status"]
    if status in _TERMINAL_LEARNING_STATUSES:
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
        raise learning.LearningError(
            f"Learning job {job_id} cannot run from status {status!r}"
        )

    context.checkpoint({"phase": "learning_queued", "job_id": job_id})
    learning.run_job(job_id)

    result = learning.get_job(job_id)
    status = result["status"]
    if status not in _TERMINAL_LEARNING_STATUSES:
        raise learning.LearningError(
            f"Learning job {job_id} returned without a terminal result (status={status!r})"
        )
    return {"job_id": job_id, "learning_status": status, "already_terminal": False}
