"""Allow-listed task adapter and recovery helpers for durable learning jobs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import learning, task_manager
from .db import learning_connection
from .task_worker import TaskContext

LEARNING_TASK_TYPE = "learning.run_job"
LEARNING_TASK_MAX_ATTEMPTS = 3
_TERMINAL_LEARNING_STATUSES = {"completed", "rejected", "cancelled"}
_ACTIVE_TASK_STATUSES = {
    "queued", "running", "pause_requested", "paused", "cancel_requested"
}


def _task_payload(job_id: int) -> dict[str, int]:
    return {"job_id": job_id}


def enqueue_learning_job(job_id: int) -> dict[str, Any]:
    """Create one durable queue entry for a learning job."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer")
    job = learning.get_job(job_id)
    if job["status"] not in {"queued", "running"}:
        raise learning.LearningError(
            f"Cannot enqueue learning job {job_id} from status {job['status']!r}"
        )
    task_manager.initialize_task_store()
    task = task_manager.create_task(
        LEARNING_TASK_TYPE,
        _task_payload(job_id),
        max_attempts=LEARNING_TASK_MAX_ATTEMPTS,
        dedupe_key=f"learning-job:{job_id}",
    )
    if task["status"] in {"succeeded", "failed", "cancelled"}:
        reconcile_pending_jobs()
    return task


def reconcile_pending_jobs() -> dict[str, int]:
    """Recover queued/stranded learning jobs and ensure one active task per job.

    The job scan, state repair, and task insertion share one SQLite write
    transaction so multiple application workers cannot enqueue duplicate jobs.
    """
    task_manager.initialize_task_store()
    queued = created = recovered = cancelled = 0
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with learning_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        jobs = conn.execute(
            """SELECT id, status FROM learning_jobs
               WHERE status IN ('queued', 'running') ORDER BY id"""
        ).fetchall()
        for job in jobs:
            job_id = int(job["id"])
            payload_json = json.dumps(
                _task_payload(job_id), ensure_ascii=False, allow_nan=False
            )
            tasks = conn.execute(
                """SELECT id, status FROM task_queue
                   WHERE task_type=? AND payload_json=? ORDER BY id DESC""",
                (LEARNING_TASK_TYPE, payload_json),
            ).fetchall()
            if any(task["status"] in _ACTIVE_TASK_STATUSES for task in tasks):
                continue
            if tasks and tasks[0]["status"] == "cancelled":
                conn.execute(
                    """UPDATE learning_jobs SET status='cancelled',
                       error=COALESCE(error, 'Cancelled task recovered during startup.'),
                       updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued', 'running')""",
                    (job_id,),
                )
                cancelled += 1
                continue

            if job["status"] == "running":
                conn.execute(
                    """UPDATE learning_jobs SET status='queued', error=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'""",
                    (job_id,),
                )
                recovered += 1

            if tasks:
                dedupe_key = f"learning-job:{job_id}:recovery:{int(tasks[0]['id'])}"
            else:
                dedupe_key = f"learning-job:{job_id}"
            conn.execute(
                """INSERT INTO task_queue(
                       task_type, payload_json, priority, max_attempts, scheduled_at, dedupe_key
                   ) VALUES (?, ?, 0, ?, ?, ?)""",
                (
                    LEARNING_TASK_TYPE,
                    payload_json,
                    LEARNING_TASK_MAX_ATTEMPTS,
                    now,
                    dedupe_key,
                ),
            )
            created += 1
            queued += 1
    return {"queued": queued, "created": created, "recovered": recovered, "cancelled": cancelled}


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
