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

    config_path = tmp_path / "machine-broker.json"
    config_value = {
        "schema_version": 1,
        "state_directory": str(state_dir),
        "socket_path": str(socket_path),
        "service_uid": os.geteuid(),
    }
    config_path.write_text(json.dumps(config_value), encoding="utf-8")
    config_path.chmod(0o600)
    monkeypatch.setattr(config, "config_file", lambda: config_path)

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

    blocked = broker.cleanup()
    assert blocked["conflict"] is True
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
