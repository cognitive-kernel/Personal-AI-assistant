from __future__ import annotations

import pytest

from app import db, learning, task_manager
from app.learning_tasks import (
    LEARNING_TASK_TYPE,
    _record_learning_escalation,
    enqueue_learning_task,
    handle_learning_job,
)
from app.learning_execution import (
    LearningExecutionLeaseLostError,
    assert_current,
    claim_execution,
    fail_if_current,
)
from app.task_worker import TaskContext
from app.task_manager import TaskManagerError


@pytest.fixture
def execution_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_domain_execution_epoch_rejects_old_worker_after_reclaim(execution_store):
    job = learning.create_job("Lease fencing regression")
    first = claim_execution(job["id"], "same-worker-label")
    assert first is not None
    assert first.epoch == 1

    # Model recovery after the queue has reclaimed the task: the replacement
    # attempt resets the domain job to queued but does not reset its epoch.
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='queued', execution_owner=NULL WHERE id=?",
            (job["id"],),
        )

    second = claim_execution(job["id"], "same-worker-label")
    assert second is not None
    assert second.epoch == 2

    with pytest.raises(LearningExecutionLeaseLostError, match="no longer current"):
        assert_current(first)
    assert_current(second)
    with pytest.raises(LearningExecutionLeaseLostError):
        fail_if_current(first, "stale worker failure")

    stored = learning.get_job(job["id"])
    assert stored["status"] == "running"
    assert stored["execution_owner"] == second.owner
    assert stored["execution_epoch"] == second.epoch


def test_learning_execution_claim_requires_exact_queue_task_contract(execution_store):
    job = learning.create_job("Queue task contract regression")
    task = task_manager.create_task("learning.run_job", {"job_id": job["id"]})
    claimed = task_manager.claim_next_task("learning-worker")
    assert claimed is not None and claimed["id"] == task["id"]
    context = TaskContext(
        task_id=task["id"],
        worker_id="learning-worker",
        lease_epoch=claimed["lease_epoch"],
    )

    lease = claim_execution(job["id"], "learning-worker:instance", task_context=context)
    assert lease is not None
    assert_current(lease, task_context=context)

    wrong_task = task_manager.create_task("learning.run_job", {"job_id": job["id"] + 1})
    wrong_claim = task_manager.claim_next_task("other-worker")
    assert wrong_claim is not None and wrong_claim["id"] == wrong_task["id"]
    wrong_context = TaskContext(
        task_id=wrong_task["id"],
        worker_id="other-worker",
        lease_epoch=wrong_claim["lease_epoch"],
    )
    with pytest.raises(TaskManagerError, match="payload does not authorize"):
        claim_execution(job["id"], "other-worker:instance", task_context=wrong_context)


def test_learning_execution_epoch_migration_is_additive(execution_store):
    job = learning.create_job("Migration preserves existing jobs")
    with db.learning_connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        stored = connection.execute(
            "SELECT status, topic, execution_owner, execution_epoch FROM learning_jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(learning_jobs)")}
    assert {"execution_owner", "execution_epoch", "execution_task_id", "execution_task_epoch"} <= columns
    assert stored["status"] == "queued"
    assert stored["topic"] == "Migration preserves existing jobs"
    assert stored["execution_owner"] is None
    assert stored["execution_epoch"] == 0
    assert {"idx_learning_jobs_execution_owner_epoch", "idx_learning_jobs_execution_task"} <= indexes


def test_duplicate_queue_task_does_not_steal_live_learning_execution(execution_store):
    job = learning.create_job("Concurrent duplicate learning task")
    first_task = task_manager.create_task(
        LEARNING_TASK_TYPE, {"job_id": job["id"]}, dedupe_key="learning-owner-first"
    )
    first_claim = task_manager.claim_next_task("worker-first")
    assert first_claim is not None and first_claim["id"] == first_task["id"]
    first_context = TaskContext(
        task_id=first_task["id"],
        worker_id="worker-first",
        lease_epoch=first_claim["lease_epoch"],
    )
    first_lease = claim_execution(
        job["id"], "task:first", task_context=first_context
    )
    assert first_lease is not None

    second_task = task_manager.create_task(
        LEARNING_TASK_TYPE, {"job_id": job["id"]}, dedupe_key="learning-owner-second"
    )
    second_claim = task_manager.claim_next_task("worker-second")
    assert second_claim is not None and second_claim["id"] == second_task["id"]
    second_context = TaskContext(
        task_id=second_task["id"],
        worker_id="worker-second",
        lease_epoch=second_claim["lease_epoch"],
    )

    result = handle_learning_job({"job_id": job["id"]}, None, second_context)
    assert result["already_running"] is True
    stored = learning.get_job(job["id"])
    assert stored["status"] == "running"
    assert stored["execution_owner"] == first_lease.owner
    assert stored["execution_task_id"] == first_task["id"]
    assert stored["execution_task_epoch"] == first_claim["lease_epoch"]


def test_stale_task_context_cannot_enqueue_or_escalate_after_reclaim(execution_store):
    first_job = learning.create_job("Original authorized learning job")
    next_job = learning.create_job("Next learning job")
    task = task_manager.create_task(
        LEARNING_TASK_TYPE,
        {"job_id": first_job["id"]},
        dedupe_key="learning-job-owner-reclaim",
    )
    first_claim = task_manager.claim_next_task("same-worker-label")
    assert first_claim is not None and first_claim["id"] == task["id"]
    stale_context = TaskContext(
        task_id=task["id"],
        worker_id="same-worker-label",
        lease_epoch=first_claim["lease_epoch"],
    )

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE task_queue SET lease_expires_at='2000-01-01T00:00:00.000000Z' WHERE id=?",
            (task["id"],),
        )
    replacement = task_manager.claim_next_task("replacement-worker")
    assert replacement is not None and replacement["id"] == task["id"]
    assert replacement["lease_epoch"] > stale_context.lease_epoch

    with pytest.raises(TaskManagerError):
        enqueue_learning_task(
            next_job["id"],
            task_context=stale_context,
            authority_job_id=first_job["id"],
        )
    assert task_manager.get_task_by_dedupe_key(
        f"learning-job:{next_job['id']}"
    ) is None

    with pytest.raises(TaskManagerError):
        _record_learning_escalation(
            task["id"],
            first_job["id"],
            "stale_attempt",
            "must not be written by the old owner",
            task_context=stale_context,
            authority_job_id=first_job["id"],
        )
    with db.learning_connection() as connection:
        escalation = connection.execute(
            "SELECT id FROM learning_escalations WHERE task_id=?",
            (task["id"],),
        ).fetchone()
    assert escalation is None
