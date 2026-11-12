from __future__ import annotations

import os
import signal
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

def test_windows_termination_rechecks_creation_time_on_open_handle(monkeypatch: pytest.MonkeyPatch):
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    expected_start = 1234.5
    terminated = []
    wait_calls = []

    def open_process(access, inherit, pid):
        return 123

    def get_process_times(handle, creation_ptr, exit_ptr, kernel_ptr, user_ptr):
        ticks = int((expected_start + 11_644_473_600) * 10_000_000)
        creation = ctypes.cast(creation_ptr, ctypes.POINTER(wintypes.FILETIME)).contents
        creation.dwLowDateTime = ticks & 0xFFFFFFFF
        creation.dwHighDateTime = (ticks >> 32) & 0xFFFFFFFF
        return 1

    def wait_for_single_object(handle, timeout):
        wait_calls.append(timeout)
        return 0x00000102 if len(wait_calls) == 1 else 0

    kernel32 = SimpleNamespace(
        OpenProcess=open_process,
        GetProcessTimes=get_process_times,
        TerminateProcess=lambda handle, code: terminated.append((handle, code)) or 1,
        WaitForSingleObject=wait_for_single_object,
        CloseHandle=lambda handle: 1,
    )
    monkeypatch.setattr(process_control.ctypes, "WinDLL", lambda *args, **kwargs: kernel32, raising=False)

    process_control._terminate_windows_process(4321, 1.0, expected_start)

    assert terminated == [(123, 1)]


def test_windows_termination_refuses_pid_reuse_after_open(monkeypatch: pytest.MonkeyPatch):
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    expected_start = 1234.5
    terminated = []

    def open_process(access, inherit, pid):
        return 123

    def get_process_times(handle, creation_ptr, exit_ptr, kernel_ptr, user_ptr):
        ticks = int((expected_start + 10.0 + 11_644_473_600) * 10_000_000)
        creation = ctypes.cast(creation_ptr, ctypes.POINTER(wintypes.FILETIME)).contents
        creation.dwLowDateTime = ticks & 0xFFFFFFFF
        creation.dwHighDateTime = (ticks >> 32) & 0xFFFFFFFF
        return 1

    kernel32 = SimpleNamespace(
        OpenProcess=open_process,
        GetProcessTimes=get_process_times,
        TerminateProcess=lambda handle, code: terminated.append((handle, code)) or 1,
        WaitForSingleObject=lambda handle, timeout: 0x00000102,
        CloseHandle=lambda handle: 1,
    )
    monkeypatch.setattr(process_control.ctypes, "WinDLL", lambda *args, **kwargs: kernel32, raising=False)

    with pytest.raises(RuntimeError, match="reused before termination"):
        process_control._terminate_windows_process(4321, 1.0, expected_start)

    assert terminated == []


@pytest.mark.skipif(
    os.name != "posix"
    or not callable(getattr(os, "pidfd_open", None))
    or not callable(getattr(signal, "pidfd_send_signal", None)),
    reason="Linux pidfd process identity support is required.",
)
def test_unresponsive_watcher_is_killed_and_reaped(tmp_path: Path):
    import os
    import signal
    import subprocess
    import sys

    script = tmp_path / "unresponsive_watcher.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('READY', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    command = [
        sys.executable, str(script), "watch",
        "--base-sha", "a" * 40,
        "--run-id", "run-42",
        "--task", "3",
        "--interval", "20",
    ]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        started_at = process_control.process_start_time(child.pid)
        metadata = _metadata(started_at=started_at)
        metadata["watcher_pid"] = child.pid
        process_control.stop_owned_watcher(metadata, script_path=script, timeout_seconds=0.1)
        child.wait(timeout=5)
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

def test_access_denied_while_reading_command_line_blocks_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    script = tmp_path / "autonomous_engineering_checkpoint.py"

    def denied_command_line(pid: int):
        raise PermissionError("command-line access denied")

    monkeypatch.setattr(process_control, "_read_process_snapshot", denied_command_line)
    with pytest.raises(RuntimeError, match="Cannot inspect checkpoint watcher PID"):
        process_control.stop_owned_watcher(_metadata(), script_path=script)
