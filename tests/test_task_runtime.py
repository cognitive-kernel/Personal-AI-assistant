import threading
import time

import pytest

from app import db, task_manager
from app.task_runtime import TaskRuntime


@pytest.fixture
def runtime_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_runtime_processes_queued_task_and_stops_cleanly(runtime_store):
    finished = threading.Event()
    task = task_manager.create_task("runtime.echo", {"value": 42})

    def handler(payload, checkpoint, context):
        context.checkpoint({"phase": "handled"})
        finished.set()
        return {"value": payload["value"]}

    runtime = TaskRuntime({"runtime.echo": handler}, poll_interval=0.05, lease_seconds=10)
    runtime.start()
    runtime.start()  # idempotent
    assert finished.wait(timeout=3)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        saved = task_manager.get_task(task["id"])
        if saved["status"] == "succeeded":
            break
        time.sleep(0.01)

    assert saved["status"] == "succeeded"
    assert saved["result"] == {"value": 42}
    assert runtime.stop(timeout=3) is True
    assert runtime.is_running is False


def test_runtime_shutdown_when_idle(runtime_store):
    runtime = TaskRuntime({"runtime.noop": lambda *_: {"ok": True}}, poll_interval=0.05)
    runtime.start()
    assert runtime.is_running is True
    assert runtime.stop(timeout=3) is True
    assert runtime.is_running is False


def test_runtime_validates_worker_and_poll_configuration():
    with pytest.raises(ValueError):
        TaskRuntime({"valid": lambda *_: {}}, worker_count=0)
    with pytest.raises(ValueError):
        TaskRuntime({"valid": lambda *_: {}}, poll_interval=0)
    with pytest.raises(ValueError):
        TaskRuntime({}, worker_count=1)



def test_new_runtime_reclaims_expired_lease_and_resumes_from_checkpoint(runtime_store):
    task = task_manager.create_task(
        "runtime.recover", {"value": "persisted"}, max_attempts=3
    )
    claimed = task_manager.claim_next_task("previous-process", lease_seconds=30)
    assert claimed["id"] == task["id"]
    task_manager.save_checkpoint(
        task["id"], "previous-process", {"phase": "before_restart", "cursor": 17}
    )
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE task_queue SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000000+00:00", task["id"]),
        )

    observed = {}

    def resume_handler(payload, checkpoint, context):
        observed["payload"] = payload
        observed["checkpoint"] = checkpoint
        context.checkpoint({"phase": "resumed", "cursor": checkpoint["cursor"]})
        return {"resumed": True, "cursor": checkpoint["cursor"]}

    runtime = TaskRuntime(
        {"runtime.recover": resume_handler}, poll_interval=0.05, lease_seconds=10
    )
    runtime.start()
    deadline = time.monotonic() + 3
    saved = task_manager.get_task(task["id"])
    while time.monotonic() < deadline and saved["status"] != "succeeded":
        time.sleep(0.01)
        saved = task_manager.get_task(task["id"])
    assert runtime.stop(timeout=3) is True

    assert observed["payload"] == {"value": "persisted"}
    assert observed["checkpoint"] == {"phase": "before_restart", "cursor": 17}
    assert saved["status"] == "succeeded"
    assert saved["attempts"] == 2
    assert saved["checkpoint"] == {"phase": "resumed", "cursor": 17}
    assert saved["result"] == {"resumed": True, "cursor": 17}



def test_runtime_runs_maintenance_callback_after_queue_reconciliation(runtime_store):
    called = threading.Event()

    def maintenance():
        called.set()

    runtime = TaskRuntime(
        {"runtime.noop": lambda *_: {"ok": True}},
        poll_interval=0.05,
        maintenance_callback=maintenance,
        maintenance_interval=30,
    )
    runtime.start()
    assert called.wait(timeout=3)
    assert runtime.stop(timeout=3) is True


def test_runtime_serializes_maintenance_callback_across_workers(runtime_store):
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    max_active = 0

    def maintenance():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            entered.set()
        release.wait(timeout=1)
        with lock:
            active -= 1

    runtime = TaskRuntime(
        {"runtime.noop": lambda *_: {"ok": True}},
        worker_count=3,
        poll_interval=0.05,
        maintenance_callback=maintenance,
        maintenance_interval=1,
    )
    runtime.start()
    assert entered.wait(timeout=3)
    time.sleep(0.1)
    release.set()
    assert runtime.stop(timeout=3) is True
    assert max_active == 1



def test_cleanup_maintenance_never_marks_an_active_task_terminal(runtime_store):
    started = threading.Event()
    release = threading.Event()
    callback_done = threading.Event()
    observed_terminal_ids = []

    task = task_manager.create_task("sandbox.long-running", {"value": 1})

    def long_handler(payload, checkpoint, context):
        started.set()
        assert release.wait(timeout=3)
        return {"finished": True}

    def maintenance():
        if not started.wait(timeout=3):
            return
        observed_terminal_ids.extend(
            task_manager.list_terminal_task_ids(min_terminal_age_seconds=0)
        )
        callback_done.set()

    runtime = TaskRuntime(
        {"sandbox.long-running": long_handler, "runtime.noop": lambda *_: {"ok": True}},
        worker_count=2,
        poll_interval=0.05,
        maintenance_callback=maintenance,
        maintenance_interval=1,
    )
    runtime.start()
    try:
        assert started.wait(timeout=3)
        assert callback_done.wait(timeout=3)
        assert task["id"] not in observed_terminal_ids
        assert task_manager.get_task(task["id"])["status"] == "running"
    finally:
        release.set()
        assert runtime.stop(timeout=3) is True


def test_expired_sandbox_lease_is_reconciled_before_cleanup_callback(runtime_store):
    task = task_manager.create_task(
        "sandbox.run_python",
        {"source_code": "print('never replayed')"},
        max_attempts=1,
    )
    claimed = task_manager.claim_next_task("crashed-worker", lease_seconds=30)
    assert claimed["id"] == task["id"]
    task_manager.save_checkpoint(
        task["id"], "crashed-worker",
        {"phase": "sandbox_started", "source_sha256": "a" * 64},
    )
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE task_queue SET lease_expires_at=?, updated_at=? WHERE id=?",
            (
                "2000-01-01T00:00:00.000000+00:00",
                "2000-01-01T00:00:00.000000+00:00",
                task["id"],
            ),
        )

    observed = {}
    callback_done = threading.Event()

    def maintenance():
        saved = task_manager.get_task(task["id"])
        observed["status"] = saved["status"]
        observed["terminal_ids"] = task_manager.list_terminal_task_ids(
            min_terminal_age_seconds=0
        )
        callback_done.set()

    runtime = TaskRuntime(
        {"runtime.noop": lambda *_: {"ok": True}},
        poll_interval=0.05,
        maintenance_callback=maintenance,
        maintenance_interval=1,
    )
    runtime.start()
    try:
        assert callback_done.wait(timeout=3)
        saved = task_manager.get_task(task["id"])
        assert observed["status"] == "failed"
        assert task["id"] in observed["terminal_ids"]
        assert saved["checkpoint"]["phase"] == "sandbox_started"
        assert saved["attempts"] == 1
    finally:
        assert runtime.stop(timeout=3) is True



def test_blocked_maintenance_does_not_block_task_workers(runtime_store):
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    task_finished = threading.Event()

    def maintenance():
        maintenance_started.set()
        release_maintenance.wait(timeout=3)

    runtime = TaskRuntime(
        {"runtime.echo": lambda *_: task_finished.set() or {"ok": True}},
        poll_interval=0.05,
        maintenance_callback=maintenance,
        maintenance_interval=30,
    )
    runtime.start()
    try:
        assert maintenance_started.wait(timeout=3)
        task_manager.create_task("runtime.echo", {})
        assert task_finished.wait(timeout=3)
    finally:
        release_maintenance.set()
        assert runtime.stop(timeout=3) is True


def test_runtime_rolls_back_threads_when_startup_fails_partway(runtime_store, monkeypatch):
    from app import task_runtime

    created = []

    class FakeThread:
        def __init__(self, *, target, args=(), name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.alive = False
            created.append(self)

        def start(self):
            if len(created) == 2:
                raise RuntimeError("simulated thread creation failure")
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.alive = False

    monkeypatch.setattr(task_runtime.threading, "Thread", FakeThread)
    runtime = TaskRuntime(
        {"runtime.start-failure": lambda *_: {"ok": True}},
        worker_count=2,
        poll_interval=0.05,
    )

    with pytest.raises(RuntimeError, match="simulated thread creation failure"):
        runtime.start()

    assert len(created) == 2
    assert all(not thread.is_alive() for thread in created)
    assert runtime.is_running is False
