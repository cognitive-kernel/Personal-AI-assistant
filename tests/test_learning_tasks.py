import pytest

from app import db, learning, task_manager
from app.learning_tasks import LEARNING_TASK_TYPE, handle_learning_job
from app.task_worker import TaskContext


@pytest.fixture
def learning_task_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def _claimed_context(job_id):
    task = task_manager.create_task(LEARNING_TASK_TYPE, {"job_id": job_id}, max_attempts=2)
    claimed = task_manager.claim_next_task("worker-1")
    assert claimed["id"] == task["id"]
    return TaskContext(task_id=task["id"], worker_id="worker-1"), task


def test_learning_task_handler_persists_real_learning_outcome(
    learning_task_store, monkeypatch
):
    job = learning.create_job("Task handler outcome")

    def fake_run_job(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", fake_run_job)
    context, task = _claimed_context(job["id"])

    result = handle_learning_job({"job_id": job["id"]}, None, context)

    assert result == {
        "job_id": job["id"],
        "learning_status": "completed",
        "already_terminal": False,
    }
    assert task_manager.get_task(task["id"])["status"] == "running"
    assert task_manager.get_task(task["id"])["checkpoint"] == {
        "phase": "learning_queued",
        "job_id": job["id"],
    }


def test_learning_task_handler_recovers_failed_job_for_queue_retry(
    learning_task_store, monkeypatch
):
    job = learning.create_job("Retry failed learning")
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='failed', error='temporary model failure' WHERE id=?",
            (job["id"],),
        )

    def fake_run_job(job_id):
        with db.learning_connection() as conn:
            row = conn.execute(
                "SELECT status FROM learning_jobs WHERE id=?", (job_id,)
            ).fetchone()
            assert row["status"] == "running"
            conn.execute(
                "UPDATE learning_jobs SET status='completed', error=NULL WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", fake_run_job)
    context, _task = _claimed_context(job["id"])

    result = handle_learning_job({"job_id": job["id"]}, None, context)

    assert result["learning_status"] == "completed"
    assert learning.get_job(job["id"])["status"] == "completed"


def test_learning_task_handler_does_not_repeat_terminal_job(
    learning_task_store, monkeypatch
):
    job = learning.create_job("Already completed learning")
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed' WHERE id=?", (job["id"],)
        )

    def unexpected_run(_job_id):
        raise AssertionError("Terminal learning jobs must not run again")

    monkeypatch.setattr(learning, "run_job", unexpected_run)
    context, _task = _claimed_context(job["id"])

    result = handle_learning_job({"job_id": job["id"]}, None, context)

    assert result["already_terminal"] is True
    assert result["learning_status"] == "completed"


@pytest.mark.parametrize("payload", [{}, {"job_id": True}, {"job_id": 0}, {"job_id": "3"}])
def test_learning_task_handler_rejects_invalid_job_ids(
    learning_task_store, payload
):
    job = learning.create_job("Invalid task payload")
    context, _task = _claimed_context(job["id"])
    with pytest.raises(ValueError, match="positive integer job_id"):
        handle_learning_job(payload, None, context)
