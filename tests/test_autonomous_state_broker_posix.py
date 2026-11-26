from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "scripts" / "autonomous_state_broker_service_posix.py"


@pytest.fixture
def posix_broker(monkeypatch, tmp_path):
    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        pytest.skip("POSIX Unix-domain sockets are not available on this platform.")

    from app import autonomous_engineering_state_broker_config as config
    from app import autonomous_engineering_state_broker as broker

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    socket_dir = tmp_path / "broker-socket"
    socket_dir.mkdir(mode=0o755)
    socket_dir.chmod(0o755)
    socket_path = socket_dir / "broker.sock"

    config_value = {
        "schema_version": 1,
        "state_directory": str(state_dir),
        "socket_path": str(socket_path),
        "service_uid": os.geteuid(),
    }
    # The integration test runs without root privileges. Exercise the actual
    # Unix socket client while replacing only the already-validated machine
    # configuration reader; production config ownership checks stay strict.
    monkeypatch.setattr(config, "load_config", lambda missing_ok=True: config_value)

    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVICE),
            "--state-dir",
            str(state_dir),
            "--socket-path",
            str(socket_path),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            pytest.fail(f"POSIX state broker exited during startup: {stdout}\n{stderr}")
        if socket_path.exists() and stat.S_ISSOCK(socket_path.lstat().st_mode):
            break
        time.sleep(0.025)
    else:
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        pytest.fail(f"POSIX state broker did not create its socket: {stdout}\n{stderr}")

    try:
        yield state_dir, socket_path, broker
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if socket_path.exists():
            socket_path.unlink()


def test_posix_broker_health_and_progress_use_one_shared_socket(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    current = {
        "run_id": "123",
        "status": "running",
        "phase_label": "تست",
        "activity": "اجرای تست Broker",
        "percent": 37,
        "updated_at": "2026-11-18T00:00:00Z",
    }
    (state_dir / "current.json").write_text(json.dumps(current), encoding="utf-8")
    (state_dir / "history.jsonl").write_text(
        json.dumps({"type": "project_snapshot", "date": "2026-11-18", "engineering_percent": 30}) + "\n",
        encoding="utf-8",
    )
    (state_dir / "events.jsonl").write_text(
        json.dumps({
            "type": "progress_event",
            "updated_at": current["updated_at"],
            "phase_label": "تست",
            "activity": "اجرای تست Broker",
            "percent": 37,
            "status": "running",
            "secret": "must-not-leak",
        }) + "\n",
        encoding="utf-8",
    )

    assert broker.health() is True
    snapshot = broker.progress()
    assert snapshot["current"] == current
    assert snapshot["history"][0]["date"] == "2026-11-18"
    assert snapshot["events"] == [{
        "updated_at": current["updated_at"],
        "phase_label": "تست",
        "activity": "اجرای تست Broker",
        "percent": 37,
        "status": "running",
    }]
    assert "secret" not in repr(snapshot)



def test_posix_broker_pending_work_and_cleanup_preserve_durable_state(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "pending-manual-task.txt").write_text("private task", encoding="utf-8")
    (state_dir / "pending-autonomous-pr.json").write_text('{"number":123}', encoding="utf-8")
    (state_dir / "current.json").write_text('{"status":"running"}', encoding="utf-8")
    (state_dir / "events.jsonl").write_text('{"type":"progress_event"}\n', encoding="utf-8")
    (state_dir / "history.jsonl").write_text('{"type":"project_snapshot"}\n', encoding="utf-8")
    (state_dir / "scorecard-baseline.json").write_text('{"date":"2026-11-18"}', encoding="utf-8")

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is True
    assert pending["markers"]["pending_task"] is True
    assert pending["markers"]["pending_pr"] is True
    assert "private task" not in repr(pending)

    with pytest.raises(broker.StateBrokerConflict, match="Durable task state is pending") as conflict:
        broker.cleanup()
    assert any("pending-manual-task.txt" in item for item in conflict.value.blockers)
    assert any("pending-autonomous-pr.json" in item for item in conflict.value.blockers)
    assert (state_dir / "current.json").exists()
    assert (state_dir / "events.jsonl").exists()

    (state_dir / "pending-manual-task.txt").unlink()
    (state_dir / "pending-autonomous-pr.json").unlink()
    cleaned = broker.cleanup()
    assert cleaned["conflict"] is False
    assert cleaned["cleaned"] is True
    assert (state_dir / "current.json").read_text(encoding="utf-8") == '{"status":"running"}'
    assert (state_dir / "events.jsonl").read_text(encoding="utf-8") == '{"type":"progress_event"}\n'
    assert {"current.json", "events.jsonl"} <= set(cleaned["preserved"])
    assert (state_dir / "history.jsonl").exists()
    assert (state_dir / "scorecard-baseline.json").exists()

def test_posix_broker_diagnostics_are_run_scoped_and_bounded(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    directory = state_dir / "run-diagnostics"
    directory.mkdir()
    payload = {
        "schema_version": 1,
        "run_id": "4321",
        "updated_at": "2026-11-18T00:00:00Z",
        "diagnostics": [{
            "stage": "test",
            "summary": "safe summary",
            "detail": "x" * 20_000,
            "task": 4,
            "attempt": 2,
            "private_key": "must-not-leak",
        }],
    }
    (directory / "4321.json").write_text(json.dumps(payload), encoding="utf-8")

    result = broker.diagnostics(4321)
    assert result is not None
    assert result["run_id"] == "4321"
    assert len(result["diagnostics"][0]["detail"]) == 12_000
    assert "private_key" not in repr(result)
    assert broker.diagnostics(9876) is None


def test_posix_broker_rejects_non_allowlisted_endpoints_without_socket_access(posix_broker):
    _state_dir, _socket_path, broker = posix_broker

    with pytest.raises(ValueError, match="Unsupported state-broker endpoint"):
        broker.request("GET", "/v1/files?path=/etc/passwd")



def _write_posix_checkpoint_fixture(state_dir: Path, run_id: str = "run-123"):
    patch = b"private checkpoint patch bytes"
    stem = f"worktree-checkpoint-{run_id}-1"
    (state_dir / f"{stem}.patch").write_bytes(patch)
    metadata = {
        "schema_version": 4,
        "run_id": run_id,
        "task": 1,
        "base_sha": "e" * 40,
        "updated_at": 1763452800.25,
        "patch_bytes": len(patch),
        "watcher_pid": None,
        "watcher_started_at": None,
        "watcher_ready": False,
    }
    (state_dir / f"{stem}.json").write_text(json.dumps(metadata), encoding="utf-8")
    index = {
        "schema_version": 1,
        "active": True,
        "run_id": run_id,
        "task": 1,
        "base_sha": metadata["base_sha"],
        "updated_at": metadata["updated_at"],
        "patch_bytes": len(patch),
        "plan_available": False,
    }
    index_path = state_dir / "latest-worktree-checkpoint.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return stem, index_path, patch


def test_posix_broker_exposes_only_verified_checkpoint_summary_and_blocks_cleanup(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    stem, index_path, patch = _write_posix_checkpoint_fixture(state_dir)

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is True
    assert pending["checkpoint"]["available"] is True
    assert pending["checkpoint"]["verified"] is True
    assert pending["checkpoint"]["run_id"] == "run-123"
    assert pending["checkpoint"]["task"] == 1
    assert pending["checkpoint"]["patch_bytes"] == len(patch)
    assert patch.decode() not in repr(pending)
    assert str(state_dir) not in repr(pending)
    assert "path" not in pending["checkpoint"]

    with pytest.raises(broker.StateBrokerConflict, match="Durable task state is pending"):
        broker.cleanup()
    assert (state_dir / f"{stem}.patch").read_bytes() == patch
    assert (state_dir / f"{stem}.json").is_file()

    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["active"] = False
    index_path.write_text(json.dumps(index), encoding="utf-8")

    empty = broker.pending_work()
    assert empty["available"] is True
    assert empty["blocked"] is False
    assert empty["checkpoint"] == {"available": False, "verified": True}

    cleaned = broker.cleanup()
    assert cleaned["conflict"] is False
    assert cleaned["cleaned"] is True
    assert (state_dir / f"{stem}.patch").read_bytes() == patch
    assert (state_dir / f"{stem}.json").is_file()


def test_posix_broker_fails_closed_on_checkpoint_pointer_mismatch(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    stem, index_path, _patch = _write_posix_checkpoint_fixture(state_dir)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["patch_bytes"] += 1
    index_path.write_text(json.dumps(index), encoding="utf-8")

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is True
    assert pending["checkpoint"]["available"] is True
    assert pending["checkpoint"]["verified"] is False
    with pytest.raises(broker.StateBrokerConflict, match="Durable task state is pending"):
        broker.cleanup()
    assert (state_dir / f"{stem}.patch").is_file()
    assert (state_dir / f"{stem}.json").is_file()



def test_posix_broker_cleanup_refuses_while_checkpoint_watcher_owns_lock(posix_broker):
    import fcntl

    state_dir, _socket_path, broker = posix_broker
    lock_path = state_dir / "autonomous-checkpoint-store.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(broker.StateBrokerConflict) as conflict:
                broker.cleanup()
            assert "checkpoint_writer_active" in conflict.value.blockers
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



def test_posix_broker_resolves_legacy_checkpoint_from_current_run_without_index(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    stem, index_path, patch = _write_posix_checkpoint_fixture(state_dir, "run-legacy")
    index_path.unlink()
    (state_dir / "current.json").write_text(
        json.dumps({"run_id": "run-legacy", "status": "completed"}),
        encoding="utf-8",
    )

    pending = broker.pending_work()
    assert pending["blocked"] is True
    assert pending["checkpoint"]["available"] is True
    assert pending["checkpoint"]["verified"] is True
    assert pending["checkpoint"]["run_id"] == "run-legacy"
    assert pending["checkpoint"]["patch_bytes"] == len(patch)
    with pytest.raises(broker.StateBrokerConflict):
        broker.cleanup()
    assert (state_dir / f"{stem}.patch").is_file()



def test_posix_broker_does_not_misclassify_completed_current_run_without_checkpoint(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "current.json").write_text(
        json.dumps({"run_id": "run-already-completed", "status": "completed"}),
        encoding="utf-8",
    )

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is False
    assert pending["checkpoint"] == {"available": False, "verified": True}



def test_posix_broker_reports_empty_checkpoint_when_current_progress_is_absent(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    assert not (state_dir / "current.json").exists()
    assert not (state_dir / "latest-worktree-checkpoint.json").exists()

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is False
    assert pending["checkpoint"] == {"available": False, "verified": True}


def _write_pending_task_fixture(state_dir: Path, task: str = "preserve this task"):
    import hashlib

    task_bytes = task.encode("utf-8")
    (state_dir / "pending-manual-task.txt").write_bytes(task_bytes)
    owner = {
        "schema_version": 1,
        "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
        "submitted_run_id": "12345",
        "submitted_attempt": 1,
        "owner_run_id": "12345",
        "owner_attempt": 1,
        "created_at": 1763500000.0,
        "claimed_at": 1763500000.0,
        "claim_count": 1,
        "source": "manual_dispatch",
    }
    owner_raw = json.dumps(owner, sort_keys=True).encode("utf-8")
    (state_dir / "pending-manual-task-owner.json").write_bytes(owner_raw)


def test_posix_broker_fails_closed_when_owner_metadata_has_no_task(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "pending-manual-task-owner.json").write_text("{}", encoding="utf-8")

    with pytest.raises(broker.StateBrokerConflict) as conflict:
        broker.request("GET", "/v1/tasks/pending")
    assert "owner_metadata_without_task" in conflict.value.blockers
    assert (state_dir / "pending-manual-task-owner.json").exists()


def test_posix_broker_pending_task_archive_restore_preserves_bytes(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    _write_pending_task_fixture(state_dir)
    task_bytes = (state_dir / "pending-manual-task.txt").read_bytes()
    owner_bytes = (state_dir / "pending-manual-task-owner.json").read_bytes()

    pending = broker.request("GET", "/v1/tasks/pending")
    assert pending["available"] is True
    assert len(pending["tasks"]) == 1
    item = pending["tasks"][0]
    assert item["status"] == "pending"
    assert "preserve this task" not in repr(pending)
    assert str(state_dir) not in repr(pending)

    archived = broker.request(
        "POST", "/v1/tasks/archive",
        body={"task_id": item["task_id"], "revision": item["revision"]},
    )
    assert archived == {"task_id": item["task_id"], "status": "archived", "preserved": True}
    assert not (state_dir / "pending-manual-task.txt").exists()
    assert not (state_dir / "pending-manual-task-owner.json").exists()

    archive_listing = broker.request("GET", "/v1/tasks/archived")
    saved = archive_listing["tasks"][0]
    assert saved["task_id"] == item["task_id"]
    assert saved["status"] == "archived"
    assert saved["source"] == "manual_dispatch"
    package = state_dir / "archived-pending-tasks" / item["task_id"]
    assert (package / "task.txt").read_bytes() == task_bytes
    assert (package / "owner.json").read_bytes() == owner_bytes

    restored = broker.request(
        "POST", "/v1/tasks/restore",
        body={"task_id": saved["task_id"], "revision": saved["revision"]},
    )
    assert restored == {"task_id": item["task_id"], "status": "restored", "preserved": True}
    assert (state_dir / "pending-manual-task.txt").read_bytes() == task_bytes
    assert (state_dir / "pending-manual-task-owner.json").read_bytes() == owner_bytes
    assert broker.request("GET", "/v1/tasks/archived")["tasks"][0]["status"] == "restored"


def test_posix_broker_task_archive_rejects_stale_revision_and_active_execution(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    _write_pending_task_fixture(state_dir)
    item = broker.request("GET", "/v1/tasks/pending")["tasks"][0]

    with pytest.raises(broker.StateBrokerConflict, match="revision is stale"):
        broker.request(
            "POST", "/v1/tasks/archive",
            body={"task_id": item["task_id"], "revision": "0" * 64},
        )
    assert (state_dir / "pending-manual-task.txt").exists()

    lock_path = state_dir / "repository-execution.lock"
    lock_path.write_bytes(b"0")
    import fcntl
    with lock_path.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(broker.StateBrokerConflict) as conflict:
                broker.request(
                    "POST", "/v1/tasks/archive",
                    body={"task_id": item["task_id"], "revision": item["revision"]},
                )
            assert "repository_execution_active" in conflict.value.blockers
            assert (state_dir / "pending-manual-task.txt").exists()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



def test_posix_broker_dispatch_admission_reservation_is_persistent_and_two_phase(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    first_id = "a" * 32
    second_id = "b" * 32

    acquired = broker.request(
        "POST", "/v1/dispatch-admission/acquire",
        body={"request_id": first_id},
    )
    assert acquired["acquired"] is True
    assert acquired["lease_seconds"] == 120
    with pytest.raises(broker.StateBrokerConflict) as conflict:
        broker.request(
            "POST", "/v1/dispatch-admission/acquire",
            body={"request_id": second_id},
        )
    assert "dispatch_reservation_active" in conflict.value.blockers

    released = broker.request(
        "POST", "/v1/dispatch-admission/complete",
        body={"request_id": first_id, "accepted": False},
    )
    assert released["completed"] is True
    acquired_again = broker.request(
        "POST", "/v1/dispatch-admission/acquire",
        body={"request_id": second_id},
    )
    assert acquired_again["acquired"] is True
    committed = broker.request(
        "POST", "/v1/dispatch-admission/complete",
        body={"request_id": second_id, "accepted": True},
    )
    assert committed["completed"] is True
    state = json.loads((state_dir / "workflow-dispatch-admission.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["pending"] is None
    assert state["last_accepted_at"] > 0

    with pytest.raises(broker.StateBrokerConflict) as grace:
        broker.request(
            "POST", "/v1/dispatch-admission/acquire",
            body={"request_id": "c" * 32},
        )
    assert "dispatch_recently_accepted" in grace.value.blockers


def test_posix_broker_fails_closed_when_dispatch_admission_state_is_corrupt(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "workflow-dispatch-admission.json").write_text(
        '{"schema_version":1,"last_accepted_at":"invalid","pending":null}',
        encoding="utf-8",
    )
    with pytest.raises(broker.StateBrokerError, match="dispatch_admission_state_invalid"):
        broker.request(
            "POST", "/v1/dispatch-admission/acquire",
            body={"request_id": "d" * 32},
        )



def test_posix_broker_recovers_expired_dispatch_reservation_with_fresh_grace(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    now = time.time()
    (state_dir / "workflow-dispatch-admission.json").write_text(json.dumps({
        "schema_version": 1,
        "last_accepted_at": 0.0,
        "pending": {
            "request_id": "e" * 32,
            "created_at": now - 121.0,
            "lease_expires_at": now - 1.0,
        },
    }), encoding="utf-8")
    with pytest.raises(broker.StateBrokerConflict) as conflict:
        broker.request(
            "POST", "/v1/dispatch-admission/acquire",
            body={"request_id": "f" * 32},
        )
    assert "dispatch_recently_accepted" in conflict.value.blockers
    recovered = json.loads((state_dir / "workflow-dispatch-admission.json").read_text(encoding="utf-8"))
    assert recovered["pending"] is None
    assert recovered["last_accepted_at"] >= now - 1.0


def test_socket_is_not_world_accessible(posix_broker):
    _, socket_path, _ = posix_broker
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o660
