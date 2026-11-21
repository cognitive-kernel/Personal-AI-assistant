"""Fenced ownership for one durable learning-job execution attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import learning_connection
from .task_manager import TaskManagerError

LEARNING_JOB_TASK_TYPE = "learning.run_job"


class LearningExecutionLeaseLostError(RuntimeError):
    """Raised when a stale execution epoch attempts to continue or commit."""


@dataclass(frozen=True)
class LearningExecutionLease:
    job_id: int
    owner: str
    epoch: int


def _assert_current_in_connection(connection, lease: LearningExecutionLease) -> None:
    row = connection.execute(
        "SELECT status, execution_owner, execution_epoch FROM learning_jobs WHERE id=?",
        (lease.job_id,),
    ).fetchone()
    if (
        row is None
        or row["status"] != "running"
        or row["execution_owner"] != lease.owner
        or int(row["execution_epoch"]) != lease.epoch
    ):
        raise LearningExecutionLeaseLostError(
            f"Learning job {lease.job_id} execution lease is no longer current."
        )


def assert_task_and_execution(
    connection,
    lease: LearningExecutionLease,
    task_context: Any | None,
) -> None:
    """Validate queue authority and domain epoch in the caller's DB transaction."""
    if task_context is not None:
        task_context.assert_current(
            connection,
            expected_task_type=LEARNING_JOB_TASK_TYPE,
            expected_payload={"job_id": lease.job_id},
        )
    _assert_current_in_connection(connection, lease)


def claim_execution(
    job_id: int,
    owner: str,
    *,
    task_context: Any | None = None,
) -> LearningExecutionLease | None:
    """Claim a queued job and advance its monotonic epoch atomically."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer.")
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise ValueError("execution owner must contain 1–200 characters.")
    normalized_owner = owner.strip()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if task_context is not None:
            task_context.assert_current(
                connection,
                expected_task_type=LEARNING_JOB_TASK_TYPE,
                expected_payload={"job_id": job_id},
            )
        row = connection.execute(
            "SELECT status, execution_epoch FROM learning_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["status"] != "queued":
            return None
        epoch = int(row["execution_epoch"]) + 1
        updated = connection.execute(
            """UPDATE learning_jobs
               SET status='running', execution_owner=?, execution_epoch=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='queued' AND execution_epoch=?""",
            (normalized_owner, epoch, job_id, int(row["execution_epoch"])),
        )
        if updated.rowcount != 1:
            return None
    return LearningExecutionLease(job_id, normalized_owner, epoch)


def assert_current(
    lease: LearningExecutionLease,
    *,
    connection=None,
    task_context: Any | None = None,
) -> None:
    """Reject stale queue or domain ownership, optionally in the caller's transaction."""
    if connection is not None:
        assert_task_and_execution(connection, lease, task_context)
        return
    with learning_connection() as owned_connection:
        assert_task_and_execution(owned_connection, lease, task_context)


def fail_if_current(
    lease: LearningExecutionLease,
    error: str,
    *,
    task_context: Any | None = None,
) -> bool:
    """Set a job failed only while the exact queue/domain ownership is current."""
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be non-empty text.")
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if task_context is not None:
            task_context.assert_current(
                connection,
                expected_task_type=LEARNING_JOB_TASK_TYPE,
                expected_payload={"job_id": lease.job_id},
            )
        updated = connection.execute(
            """UPDATE learning_jobs
               SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='running' AND execution_owner=? AND execution_epoch=?""",
            (error[:1000], lease.job_id, lease.owner, lease.epoch),
        )
    return updated.rowcount == 1


def cancel_if_current(
    lease: LearningExecutionLease,
    reason: str = "user_cancelled",
    *,
    task_context: Any | None = None,
) -> bool:
    """Cancel a job only while the exact queue/domain ownership is current."""
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if task_context is not None:
            task_context.assert_current(
                connection,
                expected_task_type=LEARNING_JOB_TASK_TYPE,
                expected_payload={"job_id": lease.job_id},
            )
        updated = connection.execute(
            """UPDATE learning_jobs
               SET status='cancelled', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='running' AND execution_owner=? AND execution_epoch=?""",
            (reason[:1000], lease.job_id, lease.owner, lease.epoch),
        )
    return updated.rowcount == 1
