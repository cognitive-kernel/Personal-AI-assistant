from __future__ import annotations

from pathlib import Path

import psutil
import pytest

from scripts import autonomous_engineering_checkpoint_process as process_control


def _metadata(script_path: Path, *, started_at: float = 1234.5) -> dict[str, object]:
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


class FakeProcess:
    def __init__(self, *, created: float = 1234.5, command: list[str] | None = None):
        self.created = created
        self.command = command or []
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def create_time(self) -> float:
        return self.created

    def cmdline(self) -> list[str]:
        return self.command

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return 0


def test_stops_only_exact_run_task_base_and_process_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    process = FakeProcess(command=_command(script))
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    process_control.stop_owned_watcher(
        _metadata(script),
        script_path=script,
    )

    assert process.terminated is True
    assert process.killed is False
    assert process.wait_calls == 1


def test_access_denied_blocks_recovery_instead_of_being_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"

    def denied(pid: int):
        raise psutil.AccessDenied(pid=pid)

    monkeypatch.setattr(process_control.psutil, "Process", denied)

    with pytest.raises(RuntimeError, match="Cannot inspect checkpoint watcher PID"):
        process_control.stop_owned_watcher(_metadata(script), script_path=script)


def test_reused_pid_is_not_terminated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    process = FakeProcess(created=9999.0, command=["unrelated.exe"])
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    process_control.stop_owned_watcher(_metadata(script), script_path=script)

    assert process.terminated is False
    assert process.killed is False


def test_matching_command_with_mismatched_creation_time_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    process = FakeProcess(created=9999.0, command=_command(script))
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    with pytest.raises(RuntimeError, match="ownership is ambiguous"):
        process_control.stop_owned_watcher(_metadata(script), script_path=script)

    assert process.terminated is False


def test_live_pid_with_mismatched_command_blocks_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    process = FakeProcess(command=["python.exe", "different.py", "watch"])
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    with pytest.raises(RuntimeError, match="command line does not match"):
        process_control.stop_owned_watcher(_metadata(script), script_path=script)

    assert process.terminated is False


def test_legacy_active_watcher_without_start_identity_blocks_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    metadata = _metadata(script)
    metadata.pop("watcher_started_at")
    process = FakeProcess(command=_command(script))
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    with pytest.raises(RuntimeError, match="legacy metadata without a process-start identity"):
        process_control.stop_owned_watcher(metadata, script_path=script)

    assert process.terminated is False


def test_missing_watcher_process_is_already_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"

    def missing(pid: int):
        raise psutil.NoSuchProcess(pid=pid)

    monkeypatch.setattr(process_control.psutil, "Process", missing)

    process_control.stop_owned_watcher(_metadata(script), script_path=script)


def test_unresponsive_watcher_is_killed_and_reaped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    script = tmp_path / "autonomous_engineering_checkpoint.py"
    process = FakeProcess(command=_command(script))

    def wait(timeout: float | None = None) -> int:
        process.wait_calls += 1
        if process.wait_calls == 1:
            raise psutil.TimeoutExpired(timeout)
        return 0

    process.wait = wait  # type: ignore[method-assign]
    monkeypatch.setattr(process_control.psutil, "Process", lambda pid: process)

    process_control.stop_owned_watcher(_metadata(script), script_path=script)

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2
