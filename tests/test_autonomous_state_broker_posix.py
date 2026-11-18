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
    assert not (state_dir / "current.json").exists()
    assert not (state_dir / "events.jsonl").exists()
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
            result = broker.cleanup()
            assert result["conflict"] is True
            assert "checkpoint_writer_active" in result["blockers"]
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



def test_posix_broker_fails_closed_when_legacy_current_progress_is_corrupt(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "current.json").write_text("{broken", encoding="utf-8")

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is True
    assert pending["checkpoint"]["available"] is True
    assert pending["checkpoint"]["verified"] is False
    assert pending["checkpoint"]["reason"] == "current_progress_unreadable"

    with pytest.raises(broker.StateBrokerConflict):
        broker.cleanup()
    assert (state_dir / "current.json").read_text(encoding="utf-8") == "{broken"



def test_posix_broker_accepts_legacy_current_progress_without_run_id_as_no_checkpoint(posix_broker):
    state_dir, _socket_path, broker = posix_broker
    (state_dir / "current.json").write_text(
        json.dumps({
            "status": "completed",
            "phase_label": "پایان چرخه",
            "updated_at": "2026-11-18T10:00:00Z",
        }),
        encoding="utf-8",
    )

    pending = broker.pending_work()
    assert pending["available"] is True
    assert pending["blocked"] is False
    assert pending["checkpoint"] == {"available": False, "verified": True}
