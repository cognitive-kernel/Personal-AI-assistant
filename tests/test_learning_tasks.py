import pytest

from app import db, learning, task_manager
from app.learning_tasks import (
    LEARNING_TASK_TYPE,
    cancel_learning_task,
    enqueue_learning_task,
    handle_learning_job,
    reconcile_queued_learning_tasks,
)
from app.task_worker import PermanentTaskError, RetryableTaskError, TaskContext


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


def test_learning_task_missing_domain_record_is_permanent(learning_task_store):
    context, _task = _claimed_context(999999)

    with pytest.raises(PermanentTaskError, match="does not exist"):
        handle_learning_job({"job_id": 999999}, None, context)


def test_learning_task_classifies_model_failure_as_retryable(
    learning_task_store, monkeypatch
):
    job = learning.create_job("Retry transient learning failure")

    def fail(_job_id):
        raise learning.LearningError(
            "temporary failure",
            ) from __import__("app.ollama", fromlist=["OllamaError"]).OllamaError(
                "connection timed out", retryable=True
            )

    monkeypatch.setattr(learning, "run_job", fail)
    context, _task = _claimed_context(job["id"])

    with pytest.raises(RetryableTaskError, match="retry may succeed"):
        handle_learning_job({"job_id": job["id"]}, None, context)


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


@pytest.mark.parametrize("stale_status", ["failed", "running"])
def test_learning_task_handler_recovers_stale_job_for_queue_retry(
    learning_task_store, monkeypatch, stale_status
):
    job = learning.create_job("Retry stale learning")
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status=?, error='temporary worker interruption' WHERE id=?",
            (stale_status, job["id"]),
        )

    def fake_run_job(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='running' WHERE id=?", (job_id,)
            )
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



def test_learning_enqueue_is_idempotent_and_payload_contains_only_job_id(learning_task_store):
    job = learning.create_job("Durable enqueue")
    first = enqueue_learning_task(job["id"])
    second = enqueue_learning_task(job["id"])

    assert first["id"] == second["id"]
    assert first["task_type"] == LEARNING_TASK_TYPE
    assert first["payload"] == {"job_id": job["id"]}
    assert first["dedupe_key"] == f"learning-job:{job['id']}"


def test_reconciliation_creates_missing_task_for_queued_job(learning_task_store):
    job = learning.create_job("Recover orphan queued job")

    assert reconcile_queued_learning_tasks() == 1
    tasks = task_manager.list_tasks()
    matching = [item for item in tasks if item["payload"] == {"job_id": job["id"]}]

    assert len(matching) == 1
    assert matching[0]["task_type"] == LEARNING_TASK_TYPE


def test_reconciliation_does_not_duplicate_existing_task(learning_task_store):
    job = learning.create_job("Already enqueued job")
    existing = enqueue_learning_task(job["id"])

    reconcile_queued_learning_tasks()
    matching = [
        item for item in task_manager.list_tasks()
        if item["payload"] == {"job_id": job["id"]}
    ]

    assert len(matching) == 1
    assert matching[0]["id"] == existing["id"]



def test_stopping_queued_learning_cancels_its_queue_task(learning_task_store):
    job = learning.create_job("Cancel queued learning")
    task = enqueue_learning_task(job["id"])

    cancelled = cancel_learning_task(job["id"])

    assert cancelled["id"] == task["id"]
    assert cancelled["status"] == "cancelled"


def test_stopping_running_learning_requests_cooperative_task_cancel(learning_task_store):
    job = learning.create_job("Cancel running learning")
    task = enqueue_learning_task(job["id"])
    claimed = task_manager.claim_next_task("worker-cancel")
    assert claimed["id"] == task["id"]

    cancelled = cancel_learning_task(job["id"])

    assert cancelled["id"] == task["id"]
    assert cancelled["status"] == "cancel_requested"


def test_cancel_learning_task_is_safe_when_no_queue_task_exists(learning_task_store):
    job = learning.create_job("No queue task yet")

    assert cancel_learning_task(job["id"]) is None
