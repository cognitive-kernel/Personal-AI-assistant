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
