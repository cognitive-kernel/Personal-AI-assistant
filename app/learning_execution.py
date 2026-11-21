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


def _assert_current_in_connection(
    connection, lease: LearningExecutionLease, task_context: Any | None = None
) -> None:
    row = connection.execute(
        """SELECT status, execution_owner, execution_epoch,
                  execution_task_id, execution_task_epoch
           FROM learning_jobs WHERE id=?""",
        (lease.job_id,),
    ).fetchone()
    if (
        row is None
        or row["status"] != "running"
        or row["execution_owner"] != lease.owner
        or int(row["execution_epoch"]) != lease.epoch
        or (
            task_context is not None
            and (
                row["execution_task_id"] != task_context.task_id
                or row["execution_task_epoch"] != task_context.lease_epoch
            )
        )
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
    _assert_current_in_connection(connection, lease, task_context)


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
                   execution_task_id=?, execution_task_epoch=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='queued' AND execution_epoch=?""",
            (
                normalized_owner, epoch,
                task_context.task_id if task_context is not None else None,
                task_context.lease_epoch if task_context is not None else None,
                job_id, int(row["execution_epoch"]),
            ),
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
        _assert_current_in_connection(connection, lease, task_context)
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
        _assert_current_in_connection(connection, lease, task_context)
        updated = connection.execute(
            """UPDATE learning_jobs
               SET status='cancelled', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='running' AND execution_owner=? AND execution_epoch=?""",
            (reason[:1000], lease.job_id, lease.owner, lease.epoch),
        )
    return updated.rowcount == 1


def has_live_queue_owner(connection, job_id: int) -> bool:
    """Return True only when the persisted execution still maps to a live queue lease.

    A direct/manual execution has no queue lease; it is treated as live rather than
    being stolen by a duplicate queue task. Legacy rows without an owner are stale.
    """
    row = connection.execute(
        """SELECT execution_owner, execution_task_id, execution_task_epoch
           FROM learning_jobs WHERE id=?""",
        (job_id,),
    ).fetchone()
    if row is None or not row["execution_owner"]:
        return False
    if row["execution_task_id"] is None or row["execution_task_epoch"] is None:
        return str(row["execution_owner"]).startswith("direct:")
    task = connection.execute(
        "SELECT status, lease_epoch, lease_expires_at FROM task_queue WHERE id=?",
        (row["execution_task_id"],),
    ).fetchone()
    if task is None:
        return False
    from .task_manager_core import _timestamp, _now
    return (
        task["status"] in {"running", "pause_requested", "cancel_requested"}
        and int(task["lease_epoch"]) == int(row["execution_task_epoch"])
        and task["lease_expires_at"] is not None
        and task["lease_expires_at"] > _timestamp(_now())
    )
