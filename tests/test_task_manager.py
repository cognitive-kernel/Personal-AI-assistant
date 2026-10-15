from datetime import datetime, timedelta, timezone

import pytest

from app.task_manager import TaskManagerError

from app import db, task_manager


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_task_lifecycle_persists_payload_checkpoint_and_result(task_store):
    created = task_manager.create_task("learn.topic", {"topic": "SQLite", "depth": 2}, priority=4)
    assert created["status"] == "queued"
    assert created["payload"] == {"topic": "SQLite", "depth": 2}
    assert created["checkpoint"] is None

    claimed = task_manager.claim_next_task("worker-a", lease_seconds=30)
    assert claimed["id"] == created["id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["lease_owner"] == "worker-a"

    saved = task_manager.save_checkpoint(created["id"], "worker-a", {"unit": 3, "cursor": "abc"})
    assert saved["checkpoint"] == {"unit": 3, "cursor": "abc"}

    completed = task_manager.complete_task(created["id"], "worker-a", {"lessons": 3})
    assert completed["status"] == "succeeded"
    assert completed["result"] == {"lessons": 3}
    assert completed["lease_owner"] is None
    assert task_manager.get_task(created["id"])["checkpoint"] == {"unit": 3, "cursor": "abc"}


def test_claim_respects_priority_and_scheduled_time(task_store):
    now = datetime.now(timezone.utc)
    later = task_manager.create_task(
        "later", {}, priority=100, scheduled_at=now + timedelta(hours=1)
    )
    lower = task_manager.create_task("ready-low", {}, priority=1)
    higher = task_manager.create_task("ready-high", {}, priority=9)

    claimed = task_manager.claim_next_task("worker")
    assert claimed["id"] == higher["id"]
    assert claimed["id"] != later["id"]
    assert task_manager.get_task(lower["id"])["status"] == "queued"


def test_failed_task_retries_only_within_attempt_limit(task_store):
    task = task_manager.create_task("retryable", {}, max_attempts=2)
    first = task_manager.claim_next_task("worker-a")
    assert first["id"] == task["id"]

    retry = task_manager.fail_task(task["id"], "worker-a", "temporary", retry=True)
    assert retry["status"] == "queued"
    assert retry["attempts"] == 1

    second = task_manager.claim_next_task("worker-b")
    assert second["id"] == task["id"]
    failed = task_manager.fail_task(task["id"], "worker-b", "still broken", retry=True)
    assert failed["status"] == "failed"
    assert failed["attempts"] == 2


def test_pause_checkpoint_resume_and_cancel(task_store):
    task = task_manager.create_task("cooperative", {})
    claimed = task_manager.claim_next_task("worker")
    assert claimed["id"] == task["id"]

    requested = task_manager.request_pause(task["id"])
    assert requested["status"] == "pause_requested"
    paused = task_manager.save_checkpoint(task["id"], "worker", {"step": 5})
    assert paused["status"] == "paused"
    assert paused["checkpoint"] == {"step": 5}

    resumed = task_manager.resume_task(task["id"])
    assert resumed["status"] == "queued"
    assert task_manager.claim_next_task("worker-2")["id"] == task["id"]

    cancelled = task_manager.request_cancel(task["id"])
    assert cancelled["status"] == "cancel_requested"
    settled = task_manager.save_checkpoint(task["id"], "worker-2", {"step": 6})
    assert settled["status"] == "cancelled"
    assert task_manager.claim_next_task("worker-3") is None


def test_expired_lease_is_reclaimed_and_retry_limit_is_enforced(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    task = task_manager.create_task("crash-recovery", {}, max_attempts=2)
    claimed = task_manager.claim_next_task("worker-a", lease_seconds=5)
    assert claimed["attempts"] == 1

    current = current + timedelta(seconds=6)
    reclaimed = task_manager.claim_next_task("worker-b", lease_seconds=5)
    assert reclaimed["id"] == task["id"]
    assert reclaimed["attempts"] == 2
    assert reclaimed["lease_owner"] == "worker-b"

    current = current + timedelta(seconds=6)
    assert task_manager.claim_next_task("worker-c") is None
    final = task_manager.get_task(task["id"])
    assert final["status"] == "failed"
    assert final["attempts"] == 2


def test_expired_pause_or_cancel_request_does_not_stick_forever(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)

    paused_task = task_manager.create_task("pause-crash", {})
    task_manager.claim_next_task("worker-a", lease_seconds=5)
    task_manager.request_pause(paused_task["id"])

    cancelled_task = task_manager.create_task("cancel-crash", {})
    # The paused worker remains leased until its deadline; another worker can claim
    # the next queued task while the pause request waits for cooperative settlement.
    claimed_cancel = task_manager.claim_next_task("worker-b", lease_seconds=5)
    assert claimed_cancel["id"] == cancelled_task["id"]
    task_manager.request_cancel(cancelled_task["id"])

    current = current + timedelta(seconds=6)
    # Expired pause requests settle to paused; expired cancel requests settle to cancelled.
    task_manager.claim_next_task("worker-c")
    assert task_manager.get_task(paused_task["id"])["status"] == "paused"
    assert task_manager.get_task(cancelled_task["id"])["status"] == "cancelled"


def test_invalid_task_data_is_rejected(task_store):
    with pytest.raises(ValueError):
        task_manager.create_task("", {})
    with pytest.raises(ValueError):
        task_manager.create_task("bad-json", {"score": float("nan")})
    with pytest.raises(ValueError):
        task_manager.create_task("bad-retries", {}, max_attempts=0)


def test_expired_worker_cannot_mutate_after_lease_lapses(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    task = task_manager.create_task("lease-fencing", {}, max_attempts=2)
    task_manager.claim_next_task("worker-a", lease_seconds=5)

    current = current + timedelta(seconds=6)
    with pytest.raises(TaskManagerError, match="lease has expired"):
        task_manager.save_checkpoint(task["id"], "worker-a", {"stale": True})

    reclaimed = task_manager.claim_next_task("worker-b", lease_seconds=30)
    assert reclaimed["id"] == task["id"]
    assert task_manager.save_checkpoint(task["id"], "worker-b", {"fresh": True})["checkpoint"] == {
        "fresh": True
    }


def test_worker_can_renew_lease_and_naive_schedule_is_rejected(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    task = task_manager.create_task("heartbeat", {})
    claimed = task_manager.claim_next_task("worker", lease_seconds=5)

    renewed = task_manager.renew_lease(task["id"], "worker", lease_seconds=50)
    assert renewed["lease_expires_at"] > claimed["lease_expires_at"]

    with pytest.raises(ValueError, match="timezone"):
        task_manager.create_task("naive-schedule", {}, scheduled_at=datetime(2026, 1, 2))
