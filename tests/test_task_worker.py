import sqlite3

import pytest

from app import db, task_manager
from app.schema_registry import LEARNING_MIGRATIONS
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
