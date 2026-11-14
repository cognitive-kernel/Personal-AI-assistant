"""Fencing tests for task-queue ownership across lease reclamation."""

from datetime import datetime, timedelta, timezone

import pytest

from app import db, task_manager, task_manager_core
from app.task_manager import TaskManagerError


@pytest.fixture
def fenced_task_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_same_worker_id_cannot_mutate_a_reclaimed_lease_epoch(
    fenced_task_store, monkeypatch
):
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager_core, "_now", lambda: now)
    task = task_manager.create_task("fencing.same-worker", {}, max_attempts=3)

    first = task_manager.claim_next_task("shared-worker", lease_seconds=5)
    assert first is not None
    assert first["lease_epoch"] == 1

    now += timedelta(seconds=6)
    second = task_manager.claim_next_task("shared-worker", lease_seconds=30)
    assert second is not None
    assert second["id"] == task["id"]
    assert second["lease_epoch"] == first["lease_epoch"] + 1

    stale_epoch = first["lease_epoch"]
    stale_mutations = (
        lambda: task_manager.renew_lease(
            task["id"], "shared-worker", lease_epoch=stale_epoch, lease_seconds=60
        ),
        lambda: task_manager.save_checkpoint(
            task["id"], "shared-worker", {"stale": True}, lease_epoch=stale_epoch
        ),
        lambda: task_manager.complete_task(
            task["id"], "shared-worker", {"stale": True}, lease_epoch=stale_epoch
        ),
        lambda: task_manager.fail_task(
            task["id"], "shared-worker", "stale failure", lease_epoch=stale_epoch
        ),
        lambda: task_manager.get_worker_control(
            task["id"], "shared-worker", lease_epoch=stale_epoch
        ),
    )
    for mutate in stale_mutations:
        with pytest.raises(TaskManagerError, match="epoch is stale"):
            mutate()

    unchanged = task_manager.get_task(task["id"])
    assert unchanged["status"] == "running"
    assert unchanged["lease_epoch"] == second["lease_epoch"]
    assert unchanged["checkpoint"] is None
    assert unchanged["result"] is None
    assert unchanged["error"] is None

    saved = task_manager.save_checkpoint(
        task["id"], "shared-worker", {"current": True},
        lease_epoch=second["lease_epoch"],
    )
    assert saved["checkpoint"] == {"current": True}
    completed = task_manager.complete_task(
        task["id"], "shared-worker", {"current": True},
        lease_epoch=second["lease_epoch"],
    )
    assert completed["status"] == "succeeded"


def test_task_worker_instances_have_unique_lease_owner_tokens():
    from app.task_worker import TaskWorker

    handlers = {"fencing.noop": lambda *_: {"ok": True}}
    first = TaskWorker("shared-worker", handlers)
    second = TaskWorker("shared-worker", handlers)

    assert first.worker_id != second.worker_id
    assert first.worker_id.startswith("shared-worker:")
    assert second.worker_id.startswith("shared-worker:")
