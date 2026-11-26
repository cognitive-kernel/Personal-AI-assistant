from __future__ import annotations

import json
import socket

from app import autonomous_engineering_native_recovery as recovery


def _seed(root):
    current = {"run_id": "123", "run_attempt": "2", "status": "running", "execution_owner_token": "a"*64}
    owner = {"schema_version": 2, "run_id": "123", "run_attempt": "2", "host": socket.gethostname(),
             "owner_token": "a"*64, "pid": 23456, "process_started_at": 1000}
    raw = json.dumps(current).encode()
    (root / "current.json").write_bytes(raw)
    (root / "repository-execution.lock").write_bytes(b"0"+json.dumps(owner).encode())
    (root / "events.jsonl").write_text("", encoding="utf-8")
    return raw


def test_stale_native_run_preserves_original_and_recovery_is_idempotent(tmp_path, monkeypatch):
    raw = _seed(tmp_path)
    monkeypatch.setattr(recovery, "inspect_lock_owner_process", lambda _: "missing")
    verify = lambda run_id: {"id": run_id, "run_attempt": 2, "status": "completed"}
    first = recovery.reconcile_progress_locked(tmp_path, verify)
    assert first.state == "stale"
    assert json.loads((tmp_path / "current.json").read_bytes())["status"] == "interrupted"
    assert next(tmp_path.glob("interrupted-progress-*.json")).read_bytes() == raw
    before = (tmp_path / "events.jsonl").read_bytes()
    assert recovery.reconcile_progress_locked(tmp_path, verify).state == "terminal"
    assert (tmp_path / "events.jsonl").read_bytes() == before


def test_native_recovery_requires_exact_remote_attempt(tmp_path, monkeypatch):
    raw = _seed(tmp_path)
    monkeypatch.setattr(recovery, "inspect_lock_owner_process", lambda _: "missing")
    decision = recovery.reconcile_progress_locked(
        tmp_path, lambda run_id: {"id": run_id, "run_attempt": 3, "status": "completed"},
    )
    assert decision.state == "unknown"
    assert decision.reason == "remote_run_attempt_mismatch"
    assert (tmp_path / "current.json").read_bytes() == raw


def test_native_recovery_refuses_live_process(tmp_path, monkeypatch):
    raw = _seed(tmp_path)
    monkeypatch.setattr(recovery, "inspect_lock_owner_process", lambda _: "alive")
    decision = recovery.reconcile_progress_locked(
        tmp_path, lambda run_id: {"id": run_id, "run_attempt": 2, "status": "completed"},
    )
    assert decision.state == "active"
    assert (tmp_path / "current.json").read_bytes() == raw
