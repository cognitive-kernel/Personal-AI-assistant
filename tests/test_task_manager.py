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



def _claim_task_in_fresh_process(paths, worker_id):
    """Spawn-safe worker: opens the same file-backed SQLite store independently."""
    from app import db as child_db, task_manager as child_task_manager

    child_db.defaults.settings_db_path = paths[0]
    child_db.defaults.learning_db_path = paths[1]
    claimed = child_task_manager.claim_next_task(worker_id, lease_seconds=30)
    return claimed["id"] if claimed is not None else None


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


def test_failed_task_retries_with_bounded_backoff_and_respects_attempt_limit(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    # Keep this lifecycle test deterministic; jitter bounds are tested separately.
    monkeypatch.setattr(task_manager.random, "uniform", lambda low, high: 2.0)
    task = task_manager.create_task("retryable", {}, max_attempts=2)
    first = task_manager.claim_next_task("worker-a")
    assert first["id"] == task["id"]

    retry = task_manager.fail_task(task["id"], "worker-a", "temporary", retry=True)
    assert retry["status"] == "queued"
    assert retry["attempts"] == 1
    assert datetime.fromisoformat(retry["scheduled_at"]) == current + timedelta(seconds=2)
    assert task_manager.claim_next_task("too-early") is None

    current += timedelta(seconds=2)
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


def test_concurrent_workers_cannot_claim_the_same_task(task_store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    task = task_manager.create_task("single-claim", {})
    barrier = Barrier(2)

    def claim(worker_id):
        barrier.wait(timeout=3)
        return task_manager.claim_next_task(worker_id, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == task["id"]



def test_sandbox_task_crash_is_terminal_after_single_attempt(task_store, monkeypatch):
    from app.sandbox_tasks import SANDBOX_TASK_TYPE

    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)
    source = "print('must not be retried after worker crash')"
    task = task_manager.create_task(
        SANDBOX_TASK_TYPE,
        {"source_code": source, "timeout_seconds": 10, "max_output_bytes": 1000},
        max_attempts=1,
    )
    claimed = task_manager.claim_next_task("sandbox-worker", lease_seconds=5)
    assert claimed["id"] == task["id"]
    assert claimed["attempts"] == 1

    checkpoint = {
        "phase": "sandbox_started",
        "source_sha256": "a" * 64,
        "execution_status": "running",
    }
    task_manager.save_checkpoint(task["id"], "sandbox-worker", checkpoint)

    # Model a process crash: the worker never settles the task and its lease expires.
    current = current + timedelta(seconds=6)
    assert task_manager.claim_next_task("replacement-worker", lease_seconds=5) is None

    recovered = task_manager.get_task(task["id"])
    assert recovered["status"] == "failed"
    assert recovered["attempts"] == 1
    assert recovered["checkpoint"] == checkpoint
    assert recovered["lease_owner"] is None
    assert "retry limit reached" in recovered["error"]
    assert task_manager.claim_next_task("another-worker") is None



def test_list_terminal_task_ids_excludes_queued_and_running_tasks(task_store):
    succeeded = task_manager.create_task("terminal.success", {})
    task_manager.claim_next_task("worker-success")
    task_manager.complete_task(succeeded["id"], "worker-success", {"ok": True})

    failed = task_manager.create_task("terminal.failure", {}, max_attempts=1)
    task_manager.claim_next_task("worker-failure")
    task_manager.fail_task(failed["id"], "worker-failure", "expected", retry=False)

    running = task_manager.create_task("not.terminal.running", {})
    queued = task_manager.create_task("not.terminal.queued", {})
    claimed_running = task_manager.claim_next_task("worker-running")
    assert claimed_running["id"] == running["id"]

    terminal_ids = task_manager.list_terminal_task_ids(limit=1000, min_terminal_age_seconds=0)
    assert succeeded["id"] in terminal_ids
    assert failed["id"] in terminal_ids
    assert queued["id"] not in terminal_ids
    assert running["id"] not in terminal_ids



def test_list_terminal_task_ids_waits_for_terminal_grace_period(task_store):
    task = task_manager.create_task("grace.success", {})
    claimed = task_manager.claim_next_task("grace-worker")
    assert claimed["id"] == task["id"]
    task_manager.complete_task(task["id"], "grace-worker", {"ok": True})

    assert task["id"] not in task_manager.list_terminal_task_ids(
        min_terminal_age_seconds=60
    )
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE task_queue SET updated_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000000+00:00", task["id"]),
        )
    assert task["id"] in task_manager.list_terminal_task_ids(
        min_terminal_age_seconds=60
    )


@pytest.mark.parametrize("age", [-1, True, 1.5])
def test_terminal_task_grace_period_validates_age(task_store, age):
    with pytest.raises(ValueError, match="non-negative integer"):
        task_manager.list_terminal_task_ids(min_terminal_age_seconds=age)


def test_checkpoint_and_expired_lease_survive_store_reinitialization(task_store, monkeypatch):
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: current)

    task = task_manager.create_task("restart-recovery", {"topic": "SQLite"}, max_attempts=2)
    first = task_manager.claim_next_task("worker-before-restart", lease_seconds=5)
    assert first["id"] == task["id"]
    checkpoint = {"phase": "source_review", "source_cursor": 2}
    task_manager.save_checkpoint(task["id"], "worker-before-restart", checkpoint)

    # Reinitializing the database/schema simulates application process restart while
    # retaining the same persistent SQLite files. No in-memory worker state is reused.
    current = current + timedelta(seconds=6)
    db.init_databases()
    task_manager.initialize_task_store()

    recovered = task_manager.claim_next_task("worker-after-restart", lease_seconds=30)
    assert recovered is not None
    assert recovered["id"] == task["id"]
    assert recovered["status"] == "running"
    assert recovered["attempts"] == 2
    assert recovered["lease_owner"] == "worker-after-restart"
    assert recovered["checkpoint"] == checkpoint

    with pytest.raises(TaskManagerError, match="not owned by this worker"):
        task_manager.save_checkpoint(task["id"], "worker-before-restart", {"phase": "stale"})



def test_independent_processes_cannot_claim_the_same_task(task_store, tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    task = task_manager.create_task("multiprocess-claim", {"only_once": True})
    paths = (
        str(tmp_path / "settings.db"),
        str(tmp_path / "learning.db"),
    )
    # spawn gives each worker a fresh interpreter and independent SQLite connection;
    # this is stronger than threads sharing the parent process/module state.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=6, mp_context=context) as pool:
        claims = list(pool.map(
            _claim_task_in_fresh_process,
            [paths] * 6,
            [f"process-worker-{index}" for index in range(6)],
        ))

    successful = [task_id for task_id in claims if task_id is not None]
    assert successful == [task["id"]]
    stored = task_manager.get_task(task["id"])
    assert stored["status"] == "running"
    assert stored["attempts"] == 1
    assert stored["lease_owner"].startswith("process-worker-")

def _crash_after_checkpoint(paths, task_id, worker_id):
    """Claim and checkpoint a task, then terminate without normal interpreter cleanup."""
    import os

    from app import db as child_db, task_manager as child_task_manager

    child_db.defaults.settings_db_path = paths[0]
    child_db.defaults.learning_db_path = paths[1]
    claimed = child_task_manager.claim_next_task(worker_id, lease_seconds=1)
    if claimed is None or claimed["id"] != task_id:
        os._exit(71)
    child_task_manager.save_checkpoint(
        task_id, worker_id, {"phase": "after_source_fetch", "cursor": 3}
    )
    os._exit(23)


def test_abrupt_worker_process_exit_recovers_checkpoint_after_lease_expiry(task_store, tmp_path, monkeypatch):
    import multiprocessing

    task = task_manager.create_task("research.answer", {"question": "SQLite durability"}, max_attempts=2)
    paths = (str(tmp_path / "settings.db"), str(tmp_path / "learning.db"))
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_after_checkpoint,
        args=(paths, task["id"], "worker-crashed"),
    )
    process.start()
    process.join(timeout=15)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("Crash simulation process did not exit in time.")
    assert process.exitcode == 23

    crashed = task_manager.get_task(task["id"])
    assert crashed is not None
    assert crashed["status"] == "running"
    assert crashed["lease_owner"] == "worker-crashed"
    assert crashed["checkpoint"] == {"phase": "after_source_fetch", "cursor": 3}

    # Advance the queue's clock beyond the lease without sleeping for the real lease.
    expired_at = datetime.fromisoformat(crashed["lease_expires_at"].replace("Z", "+00:00"))
    monkeypatch.setattr(task_manager, "_now", lambda: expired_at + timedelta(seconds=1))
    recovered = task_manager.claim_next_task("worker-restarted", lease_seconds=30)

    assert recovered is not None
    assert recovered["id"] == task["id"]
    assert recovered["status"] == "running"
    assert recovered["attempts"] == 2
    assert recovered["lease_owner"] == "worker-restarted"
    assert recovered["checkpoint"] == {"phase": "after_source_fetch", "cursor": 3}
    with pytest.raises(TaskManagerError, match="not owned by this worker"):
        task_manager.save_checkpoint(task["id"], "worker-crashed", {"phase": "stale"})


def test_claim_next_task_can_isolate_task_type_queues(task_store):
    chat = task_manager.create_task("research.answer", {"message": "hello"})
    learning = task_manager.create_task("learning.run_job", {"job_id": 42})

    claimed_chat = task_manager.claim_next_task("chat-worker", task_types=("research.answer",))
    assert claimed_chat is not None
    assert claimed_chat["id"] == chat["id"]
    assert claimed_chat["task_type"] == "research.answer"

    # The learning task remains queued until the learning lane claims it.
    assert task_manager.get_task(learning["id"])["status"] == "queued"
    claimed_learning = task_manager.claim_next_task("learning-worker", task_types=("learning.run_job",))
    assert claimed_learning is not None
    assert claimed_learning["id"] == learning["id"]
    assert claimed_learning["task_type"] == "learning.run_job"


def test_claim_next_task_can_exclude_registered_queue_types(task_store):
    known = task_manager.create_task("research.answer", {"message": "hello"})
    unknown = task_manager.create_task("unregistered.type", {"payload": "preserve"})

    claimed = task_manager.claim_next_task(
        "dead-letter-worker",
        exclude_task_types=("research.answer", "learning.run_job", "unregistered.placeholder"),
    )
    assert claimed is not None
    assert claimed["id"] == unknown["id"]
    assert claimed["task_type"] == "unregistered.type"
    assert task_manager.get_task(known["id"])["status"] == "queued"

@pytest.mark.parametrize("mutation", ["renew", "checkpoint", "complete", "fail"])
def test_worker_mutations_lock_before_validating_lease(task_store, monkeypatch, mutation):
    task = task_manager.create_task("lease-mutation-lock", {}, max_attempts=2)
    claimed = task_manager.claim_next_task("worker-a", lease_seconds=30)
    assert claimed["id"] == task["id"]

    original = task_manager._owned_running_task

    def assert_transaction_started(conn, task_id, worker_id):
        assert conn.in_transaction, "lease validation must occur inside a write transaction"
        return original(conn, task_id, worker_id)

    monkeypatch.setattr(task_manager, "_owned_running_task", assert_transaction_started)
    operations = {
        "renew": lambda: task_manager.renew_lease(task["id"], "worker-a", lease_seconds=60),
        "checkpoint": lambda: task_manager.save_checkpoint(task["id"], "worker-a", {"step": 1}),
        "complete": lambda: task_manager.complete_task(task["id"], "worker-a", {"ok": True}),
        "fail": lambda: task_manager.fail_task(task["id"], "worker-a", "expected failure"),
    }
    operations[mutation]()


@pytest.mark.parametrize("mutation", ["cancel", "pause", "resume", "priority", "reschedule"])
def test_task_control_mutations_lock_before_status_validation(task_store, monkeypatch, mutation):
    from contextlib import contextmanager

    task = task_manager.create_task("control-mutation-lock", {})
    if mutation == "resume":
        task_manager.request_pause(task["id"])

    original_connection = db.learning_connection

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        @property
        def in_transaction(self):
            return self._connection.in_transaction

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("SELECT * FROM TASK_QUEUE"):
                assert self._connection.in_transaction, (
                    "task status validation must occur inside a write transaction"
                )
            return self._connection.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    @contextmanager
    def checked_connection():
        with original_connection() as connection:
            yield ConnectionProxy(connection)

    monkeypatch.setattr(db, "learning_connection", checked_connection)
    operations = {
        "cancel": lambda: task_manager.request_cancel(task["id"]),
        "pause": lambda: task_manager.request_pause(task["id"]),
        "resume": lambda: task_manager.resume_task(task["id"]),
        "priority": lambda: task_manager.update_task_priority(task["id"], 5),
        "reschedule": lambda: task_manager.reschedule_task(
            task["id"], datetime.now(timezone.utc) + timedelta(minutes=5)
        ),
    }
    result = operations[mutation]()
    expected_status = {
        "cancel": "cancelled",
        "pause": "paused",
        "resume": "queued",
        "priority": "queued",
        "reschedule": "queued",
    }
    assert result["status"] == expected_status[mutation]
@pytest.mark.parametrize(
    ("control", "expected_status"),
    [("cancel", "cancelled"), ("pause", "paused")],
)
def test_fail_task_honors_pending_control_before_retry(task_store, control, expected_status):
    task = task_manager.create_task(f"control-before-retry-{control}", {}, max_attempts=3)
    claimed = task_manager.claim_next_task("worker-a", lease_seconds=30)
    assert claimed is not None
    assert claimed["id"] == task["id"]

    requested = (
        task_manager.request_cancel(task["id"])
        if control == "cancel"
        else task_manager.request_pause(task["id"])
    )
    assert requested["status"] == ("cancel_requested" if control == "cancel" else "pause_requested")

    settled = task_manager.fail_task(
        task["id"], "worker-a", "simulated failure before cooperative checkpoint", retry=True
    )

    assert settled["status"] == expected_status
    assert settled["error"] == "simulated failure before cooperative checkpoint"
    assert settled["lease_owner"] is None
    assert settled["lease_expires_at"] is None
    assert settled["attempts"] == 1
    assert task_manager.claim_next_task("worker-b") is None

    if control == "pause":
        resumed = task_manager.resume_task(task["id"])
        assert resumed["status"] == "queued"
        reclaimed = task_manager.claim_next_task("worker-c")
        assert reclaimed is not None
        assert reclaimed["id"] == task["id"]
        assert reclaimed["attempts"] == 2

def test_list_terminal_task_ids_keyset_pages_include_all_terminal_tasks(task_store):
    completed_ids = []
    for index in range(5):
        task = task_manager.create_task(f"terminal.page.{index}", {})
        claimed = task_manager.claim_next_task(f"worker-page-{index}")
        assert claimed is not None
        completed = task_manager.complete_task(task["id"], claimed["lease_owner"], {"ok": True})
        completed_ids.append(completed["id"])

    first = task_manager.list_terminal_task_ids(limit=2, min_terminal_age_seconds=0)
    second = task_manager.list_terminal_task_ids(
        limit=2, min_terminal_age_seconds=0, before_id=first[-1]
    )
    third = task_manager.list_terminal_task_ids(
        limit=2, min_terminal_age_seconds=0, before_id=second[-1]
    )

    assert first == completed_ids[-2:][::-1]
    assert second == completed_ids[-4:-2][::-1]
    assert third == completed_ids[:1]
    assert set(first + second + third) == set(completed_ids)


@pytest.mark.parametrize("before_id", [0, -1, True, 1.5])
def test_terminal_task_cursor_validates_positive_integer(before_id, task_store):
    with pytest.raises(ValueError, match="positive integer"):
        task_manager.list_terminal_task_ids(before_id=before_id)

