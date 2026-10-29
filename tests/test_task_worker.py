import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db, task_manager
from app.schema_registry import LEARNING_MIGRATIONS
from app.task_worker import PermanentTaskError, RetryableTaskError, TaskWorker


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


def test_worker_retries_handler_failure_with_backoff_then_marks_exhausted(worker_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    monkeypatch.setattr(task_manager.random, "uniform", lambda low, high: 2.0)
    task = task_manager.create_task("test.flaky", {}, max_attempts=2)

    def fail(_payload, _checkpoint, _context):
        raise RuntimeError("transient failure")

    worker = TaskWorker("worker-1", {"test.flaky": fail})
    assert worker.run_once() is True
    first = task_manager.get_task(task["id"])
    assert first["status"] == "queued"
    assert first["error"] == "RuntimeError: transient failure"
    assert datetime.fromisoformat(first["scheduled_at"]) == current + timedelta(seconds=2)
    assert worker.run_once() is False

    current += timedelta(seconds=2)
    assert worker.run_once() is True
    second = task_manager.get_task(task["id"])
    assert second["status"] == "failed"
    assert second["attempts"] == 2


def test_retry_backoff_uses_jitter_within_bounded_window(worker_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    monkeypatch.setattr(task_manager.random, "uniform", lambda low, high: high)
    task = task_manager.create_task("test.jitter", {}, max_attempts=2)

    worker = TaskWorker("worker-jitter", {
        "test.jitter": lambda *_: (_ for _ in ()).throw(RuntimeError("temporary")),
    })
    assert worker.run_once() is True

    saved = task_manager.get_task(task["id"])
    # First retry has a 2s exponential delay and +20% deterministic jitter.
    assert datetime.fromisoformat(saved["scheduled_at"]) == current + timedelta(seconds=2.4)


def test_worker_handler_can_explicitly_mark_domain_error_retryable(worker_store):
    task = task_manager.create_task("test.domain-retryable", {}, max_attempts=2)

    def reject_once(_payload, _checkpoint, _context):
        raise RetryableTaskError("upstream service temporarily unavailable")

    worker = TaskWorker("worker-domain-retryable", {"test.domain-retryable": reject_once})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "queued"
    assert saved["attempts"] == 1


def test_worker_handler_can_explicitly_mark_domain_error_permanent(worker_store):
    task = task_manager.create_task("test.domain-permanent", {}, max_attempts=3)

    def reject(_payload, _checkpoint, _context):
        raise PermanentTaskError("business rule cannot be satisfied")

    worker = TaskWorker("worker-domain-permanent", {"test.domain-permanent": reject})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "failed"
    assert saved["attempts"] == 1
    assert saved["error"] == "PermanentTaskError: business rule cannot be satisfied"


def test_worker_does_not_retry_permanent_input_errors(worker_store):
    task = task_manager.create_task("test.invalid-input", {}, max_attempts=3)

    def reject(_payload, _checkpoint, _context):
        raise ValueError("invalid task definition")

    worker = TaskWorker("worker-1", {"test.invalid-input": reject})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "failed"
    assert saved["attempts"] == 1
    assert saved["error"] == "ValueError: invalid task definition"


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


def test_task_creation_is_idempotent_for_matching_dedupe_key(worker_store):
    first = task_manager.create_task(
        "test.deduplicated", {"job_id": 41}, max_attempts=3, dedupe_key="learning-job:41"
    )
    repeated = task_manager.create_task(
        "test.deduplicated", {"job_id": 41}, max_attempts=3, dedupe_key="learning-job:41"
    )

    assert repeated["id"] == first["id"]
    assert len(task_manager.list_tasks()) == 1


def test_task_dedupe_key_rejects_different_payload(worker_store):
    task_manager.create_task(
        "test.deduplicated", {"job_id": 41}, dedupe_key="learning-job:41"
    )
    with pytest.raises(ValueError, match="different task definition"):
        task_manager.create_task(
            "test.deduplicated", {"job_id": 42}, dedupe_key="learning-job:41"
        )


def test_task_store_creates_dedupe_key_column_and_index(worker_store):
    with db.learning_connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_queue)")}
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(task_queue)")
        }

    assert "dedupe_key" in columns
    assert "idx_task_queue_dedupe_key" in indexes


def test_task_creation_rejects_blank_dedupe_key(worker_store):
    with pytest.raises(ValueError, match="dedupe_key"):
        task_manager.create_task("test.deduplicated", {}, dedupe_key="  ")


def test_task_store_migrates_preexisting_queue_schema(worker_store, tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy-learning.db"
    monkeypatch.setattr(db.defaults, "learning_db_path", str(legacy_path))
    with sqlite3.connect(legacy_path) as conn:
        for statement in LEARNING_MIGRATIONS[0].statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO task_queue(task_type, payload_json) VALUES (?, ?)",
            ("legacy.task", "{}"),
        )

    task_manager.initialize_task_store()
    saved = task_manager.get_task(1)
    with db.learning_connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_queue)")}

    backups = list(tmp_path.glob("legacy-learning.db.pre-migration-*.bak"))
    assert saved["task_type"] == "legacy.task"
    assert "dedupe_key" in columns
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute(
            "SELECT task_type FROM task_queue WHERE id=1"
        ).fetchone() == ("legacy.task",)



def test_cancel_requested_after_handler_work_wins_over_success_result(worker_store):
    task = task_manager.create_task("test.cancel_race", {})

    def finish_after_cancel(_payload, _checkpoint, context):
        context.checkpoint({"phase": "work_finished"})
        requested = task_manager.request_cancel(context.task_id)
        assert requested["status"] == "cancel_requested"
        return {"must_not_publish": True}

    worker = TaskWorker("worker-cancel-race", {"test.cancel_race": finish_after_cancel})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "cancelled"
    assert saved["result"] is None
    assert saved["checkpoint"] == {"phase": "work_finished"}


def test_cancel_requested_before_handler_failure_wins_over_retry(worker_store):
    task = task_manager.create_task("test.cancel_failure_race", {}, max_attempts=3)

    def fail_after_cancel(_payload, _checkpoint, context):
        task_manager.request_cancel(context.task_id)
        raise RuntimeError("late failure must not schedule a retry")

    worker = TaskWorker("worker-cancel-failure-race", {"test.cancel_failure_race": fail_after_cancel})
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "cancelled"
    assert saved["attempts"] == 1
    assert saved["result"] is None
    assert saved["error"] is None


def test_worker_retries_when_result_persistence_fails(worker_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    monkeypatch.setattr(task_manager.random, "uniform", lambda low, high: 2.0)
    task = task_manager.create_task("test.result-persistence", {}, max_attempts=2)

    complete = task_manager.complete_task
    calls = 0

    def fail_first_persist(task_id, worker_id, result):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated result persistence failure")
        return complete(task_id, worker_id, result)

    monkeypatch.setattr(task_manager, "complete_task", fail_first_persist)
    worker = TaskWorker(
        "worker-result-persistence",
        {"test.result-persistence": lambda *_: {"durable": True}},
    )

    assert worker.run_once() is True
    queued = task_manager.get_task(task["id"])
    assert queued["status"] == "queued"
    assert queued["attempts"] == 1
    assert queued["error"] == "RuntimeError: simulated result persistence failure"
    assert queued["result"] is None

    current += timedelta(seconds=2)
    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "succeeded"
    assert saved["attempts"] == 2
    assert saved["result"] == {"durable": True}



def test_worker_requeues_claimed_task_when_heartbeat_thread_cannot_start(worker_store, monkeypatch):
    task = task_manager.create_task("test.heartbeat-start-failure", {}, max_attempts=2)
    handler_calls = []

    def handler(*_args):
        handler_calls.append(True)
        return {"must_not_run": True}

    worker = TaskWorker("worker-heartbeat-start-failure", {
        "test.heartbeat-start-failure": handler,
    })

    class BrokenThread:
        def start(self):
            raise RuntimeError("simulated OS thread limit")

    monkeypatch.setattr(
        worker, "_create_heartbeat_thread", lambda *_args: BrokenThread()
    )

    assert worker.run_once() is True
    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "queued"
    assert saved["attempts"] == 1
    assert saved["result"] is None
    assert saved["error"] == "HeartbeatStartupError: RuntimeError: simulated OS thread limit"
    assert handler_calls == []



def test_worker_requeues_claimed_task_when_heartbeat_thread_construction_fails(worker_store, monkeypatch):
    task = task_manager.create_task("test.heartbeat-construction-failure", {}, max_attempts=2)
    worker_calls = []
    worker = TaskWorker("worker-heartbeat-construction-failure", {
        "test.heartbeat-construction-failure": lambda *_: worker_calls.append(True) or {"ok": True},
    })

    def fail_construction(*_args):
        raise RuntimeError("simulated thread allocation failure")

    monkeypatch.setattr(worker, "_create_heartbeat_thread", fail_construction)
    assert worker.run_once() is True

    saved = task_manager.get_task(task["id"])
    assert saved["status"] == "queued"
    assert saved["attempts"] == 1
    assert saved["result"] is None
    assert saved["error"] == "HeartbeatStartupError: RuntimeError: simulated thread allocation failure"
    assert worker_calls == []
