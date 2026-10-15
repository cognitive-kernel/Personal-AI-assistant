import pytest

from app import db, learning, task_manager
from app.task_worker import TaskWorker


@pytest.fixture
def worker_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_worker_dispatches_only_registered_handler_and_persists_result(worker_store):
    task = task_manager.create_task("test.echo", {"message": "hello"})

    def echo(payload, checkpoint, context):
        assert checkpoint is None
        context.checkpoint({"phase": "started"})
        return {"echo": payload["message"]}

    worker = TaskWorker("worker-1", {"test.echo": echo})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"echo": "hello"}
    assert saved["checkpoint"] == {"phase": "started"}
    assert worker.run_once() is False


def test_worker_fails_unknown_task_type_without_executing_payload(worker_store):
    task = task_manager.create_task("unregistered.type", {"command": "must not run"})
    worker = TaskWorker("worker-1", {"known.type": lambda *_: {"ok": True}})

    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "failed"
    assert "No registered handler" in saved["error"]


def test_worker_retries_handler_failure_then_marks_exhausted(worker_store):
    task = task_manager.create_task("test.flaky", {}, max_attempts=2)

    def fail(_payload, _checkpoint, _context):
        raise RuntimeError("transient failure")

    worker = TaskWorker("worker-1", {"test.flaky": fail})
    assert worker.run_once() is True
    first = task_manager.get_task(task["id"])
    assert first["status"] == "queued"
    assert first["error"] == "RuntimeError: transient failure"

    assert worker.run_once() is True
    second = task_manager.get_task(task["id"])
    assert second["status"] == "failed"
    assert second["attempts"] == 2


def test_worker_settles_pause_at_checkpoint(worker_store):
    task = task_manager.create_task("test.pause", {})

    def pause_at_checkpoint(_payload, _checkpoint, context):
        task_manager.request_pause(context.task_id)
        context.checkpoint({"cursor": 7})
        return {"should_not_complete": True}

    worker = TaskWorker("worker-1", {"test.pause": pause_at_checkpoint})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "paused"
    assert saved["checkpoint"] == {"cursor": 7}
    assert saved["result"] is None


def test_worker_settles_cancel_at_checkpoint(worker_store):
    task = task_manager.create_task("test.cancel", {})

    def cancel_at_checkpoint(_payload, _checkpoint, context):
        task_manager.request_cancel(context.task_id)
        context.checkpoint({"cursor": 9})
        return {"should_not_complete": True}

    worker = TaskWorker("worker-1", {"test.cancel": cancel_at_checkpoint})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "cancelled"
    assert saved["checkpoint"] == {"cursor": 9}
    assert saved["result"] is None


def test_worker_rejects_non_object_results(worker_store):
    task = task_manager.create_task("test.bad-result", {})
    worker = TaskWorker("worker-1", {"test.bad-result": lambda *_: ["not", "an", "object"]})

    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "failed"
    assert "JSON object" in saved["error"]


def test_worker_requires_an_explicit_handler_registry():
    with pytest.raises(ValueError):
        TaskWorker("worker-1", {})
    with pytest.raises(ValueError):
        TaskWorker("worker-1", {"": lambda *_: {}})


def test_worker_rejects_non_mapping_handler_registry():
    with pytest.raises(ValueError):
        TaskWorker("worker-1", ["not", "a", "mapping"])


def test_worker_renews_lease_during_long_running_handler(worker_store):
    import time

    task = task_manager.create_task("test.long-running", {}, max_attempts=1)

    def slow_handler(_payload, _checkpoint, _context):
        # Longer than the initial lease. Automatic heartbeat must preserve ownership.
        time.sleep(1.4)
        return {"finished": True}

    worker = TaskWorker("worker-long", {"test.long-running": slow_handler}, lease_seconds=1)
    assert worker.run_once() is True

    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"finished": True}
    assert saved["attempts"] == 1



def test_learning_submission_creates_durable_deduplicated_task(worker_store):
    job = learning.submit_job("Durable queue integration")
    task = task_manager.get_task(job["task_id"])

    assert task["task_type"] == "learning.generate"
    assert task["payload"] == {"job_id": job["id"]}
    assert task["dedupe_key"] == f"learning-job:{job['id']}"
    repeated = learning._enqueue_learning_job(learning.get_job(job["id"]))
    assert repeated["task_id"] == task["id"]
    assert len(task_manager.list_tasks()) == 1


def test_task_dedupe_key_rejects_different_task_definition(worker_store):
    task_manager.create_task(
        "learning.generate", {"job_id": 1}, dedupe_key="learning-job:1"
    )
    with pytest.raises(ValueError, match="different task definition"):
        task_manager.create_task(
            "learning.generate", {"job_id": 2}, dedupe_key="learning-job:1"
        )


def test_learning_handler_completes_task_from_persisted_job(worker_store, monkeypatch):
    job = learning.create_job("Handler integration")
    task = task_manager.create_task(
        "learning.generate",
        {"job_id": job["id"]},
        max_attempts=2,
        dedupe_key=f"learning-job:{job['id']}",
    )

    def complete_learning(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='completed', lesson='Generated lesson', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", complete_learning)
    worker = TaskWorker("learning-test-worker", {"learning.generate": learning.handle_learning_task})
    assert worker.run_once() is True

    saved_task = task_manager.get_task(task["id"])
    saved_job = learning.get_job(job["id"])
    assert saved_task["status"] == "succeeded"
    assert saved_task["result"] == {"job_id": job["id"], "learning_status": "completed"}
    assert saved_job["status"] == "completed"


def test_learning_reconciliation_recovers_orphaned_running_job(worker_store):
    job = learning.create_job("Recover after restart")
    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='running' WHERE id=?", (job["id"],))

    recovered = learning.reconcile_learning_tasks()
    saved_job = learning.get_job(job["id"])
    tasks = task_manager.list_tasks()

    assert recovered == {"enqueued": 1, "reset_running": 1, "marked_failed": 0}
    assert saved_job["status"] == "queued"
    assert len(tasks) == 1
    assert tasks[0]["payload"] == {"job_id": job["id"]}
