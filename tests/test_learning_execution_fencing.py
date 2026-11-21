from __future__ import annotations

import pytest

from app import db, learning, task_manager
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
    assert fail_if_current(first, "stale worker failure") is False

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
    assert {"execution_owner", "execution_epoch"} <= columns
    assert stored["status"] == "queued"
    assert stored["topic"] == "Migration preserves existing jobs"
    assert stored["execution_owner"] is None
    assert stored["execution_epoch"] == 0
    assert "idx_learning_jobs_execution_owner_epoch" in indexes
