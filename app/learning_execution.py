"""Durable fencing for one learning-job execution attempt.

The task queue owns worker leases; this module owns the domain-job execution epoch.
A worker may commit only while both its queue lease and this job epoch are current.
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import learning_connection


class LearningExecutionLeaseLostError(RuntimeError):
    """Raised when a stale learning worker attempts to continue or commit."""


@dataclass(frozen=True)
class LearningExecutionLease:
    job_id: int
    owner: str
    epoch: int


def claim_execution(job_id: int, owner: str) -> LearningExecutionLease | None:
    """Atomically claim a queued job and advance its monotonic execution epoch."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError("job_id must be a positive integer.")
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise ValueError("execution owner must contain 1–200 characters.")
    normalized_owner = owner.strip()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
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


def assert_current(lease: LearningExecutionLease) -> None:
    """Reject any worker whose owner or monotonic execution epoch is stale."""
    with learning_connection() as connection:
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


def fail_if_current(lease: LearningExecutionLease, error: str) -> bool:
    """Set a job failed only if this exact execution epoch still owns it."""
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be non-empty text.")
    with learning_connection() as connection:
        updated = connection.execute(
            """UPDATE learning_jobs
               SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='running' AND execution_owner=? AND execution_epoch=?""",
            (error[:1000], lease.job_id, lease.owner, lease.epoch),
        )
    return updated.rowcount == 1
