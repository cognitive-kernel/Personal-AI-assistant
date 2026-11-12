from __future__ import annotations

from pathlib import Path

import pytest

from scripts import autonomous_engineering_checkpoint_process as process_control


def _metadata(*, started_at: float | None = 1234.5) -> dict[str, object]:
    return {
        "schema_version": 4,
        "base_sha": "a" * 40,
        "run_id": "run-42",
        "task": 3,
        "watcher_pid": 4321,
        "watcher_started_at": started_at,
        "watcher_ready": True,
    }


def _command(script_path: Path) -> list[str]:
    return [
        "python.exe",
        str(script_path),
        "watch",
        "--base-sha", "a" * 40,
        "--run-id", "run-42",
        "--task", "3",
        "--interval", "20",
    ]


def _snapshot(*, created: float = 1234.5, command: list[str] | None = None) -> dict[str, object]:
    return {"created_at": created, "command": command or []}


def test_stops_only_exact_run_task_base_and_process_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    terminated: list[tuple[int, float]] = []
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(command=_command(script)),
    )
    monkeypatch.setattr(
        process_control, "_terminate_process",
        lambda pid, timeout, expected_started_at: terminated.append((pid, timeout, expected_started_at)),
    )

    process_control.stop_owned_watcher(_metadata(), script_path=script)

    assert terminated == [(4321, 5.0, 1234.5)]


def test_access_denied_blocks_recovery_instead_of_being_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"

    def denied(pid: int):
        raise PermissionError("access denied")

    monkeypatch.setattr(process_control, "_read_process_snapshot", denied)
    with pytest.raises(RuntimeError, match="Cannot inspect checkpoint watcher PID"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)


def test_incomplete_process_snapshot_blocks_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: {"created_at": 1234.5},
    )
    with pytest.raises(RuntimeError, match="process metadata is incomplete"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)


def test_access_denied_while_terminating_watcher_blocks_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(command=_command(script)),
    )

    def denied(pid: int, timeout: float, expected_started_at: float):
        raise PermissionError("terminate denied")

    monkeypatch.setattr(process_control, "_terminate_process", denied)
    with pytest.raises(RuntimeError, match="could not be stopped and reaped"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)


def test_reused_pid_is_not_terminated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    terminated: list[int] = []
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(created=9999.0, command=["unrelated.exe"]),
    )
    monkeypatch.setattr(process_control, "_terminate_process", lambda pid, timeout, expected_started_at: terminated.append(pid))

    process_control.stop_owned_watcher(_metadata(), script_path=script)

    assert terminated == []


def test_matching_command_with_mismatched_creation_time_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    terminated: list[int] = []
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(created=9999.0, command=_command(script)),
    )
    monkeypatch.setattr(process_control, "_terminate_process", lambda pid, timeout, expected_started_at: terminated.append(pid))

    with pytest.raises(RuntimeError, match="ownership is ambiguous"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)

    assert terminated == []


def test_live_pid_with_mismatched_command_blocks_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    terminated: list[int] = []
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(command=["python.exe", "different.py", "watch"]),
    )
    monkeypatch.setattr(process_control, "_terminate_process", lambda pid, timeout, expected_started_at: terminated.append(pid))

    with pytest.raises(RuntimeError, match="command line does not match"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)

    assert terminated == []


def test_legacy_active_watcher_without_start_identity_blocks_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(command=_command(script)),
    )

    with pytest.raises(RuntimeError, match="legacy metadata without a process-start identity"):
        process_control.stop_owned_watcher(_metadata(started_at=None), script_path=script)


def test_missing_watcher_process_is_already_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    terminated: list[int] = []
    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: None)
    monkeypatch.setattr(process_control, "_terminate_process", lambda pid, timeout, expected_started_at: terminated.append(pid))

    process_control.stop_owned_watcher(_metadata(), script_path=script)

    assert terminated == []


def test_unresponsive_watcher_blocks_recovery_and_preserves_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    monkeypatch.setattr(
        process_control, "_read_process_snapshot",
        lambda pid: _snapshot(command=_command(script)),
    )

    def timeout(pid: int, timeout: float, expected_started_at: float):
        raise TimeoutError("process did not exit")

    monkeypatch.setattr(process_control, "_terminate_process", timeout)
    with pytest.raises(RuntimeError, match="could not be stopped and reaped"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)


def test_process_start_time_uses_os_snapshot_and_rejects_missing_pid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: _snapshot(created=4321.25))
    assert process_control.process_start_time(4321) == 4321.25

    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: None)
    with pytest.raises(RuntimeError, match="process is not running"):
        process_control.process_start_time(4321)
