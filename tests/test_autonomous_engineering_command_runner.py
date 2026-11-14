from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from scripts.autonomous_engineering_command_runner import run_command


def _assert_process_stopped(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return
            if not process.is_running():
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.05)
    raise AssertionError(f"Command descendant {pid} survived its owning command.")


def _spawn_child_command(pid_file: Path, *, parent_sleeps: bool) -> list[str]:
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(pid_file)) + ",'w').write(str(child.pid)); "
        + ("time.sleep(60)" if parent_sleeps else "")
    )
    return [sys.executable, "-c", parent_code]


def test_timeout_terminates_command_and_all_descendants(tmp_path: Path):
    pid_file = tmp_path / "timeout-child.pid"

    with pytest.raises(subprocess.TimeoutExpired):
        run_command(
            _spawn_child_command(pid_file, parent_sleeps=True),
            cwd=tmp_path,
            env=None,
            capture=True,
            timeout_seconds=1,
        )

    assert pid_file.is_file()
    _assert_process_stopped(int(pid_file.read_text(encoding="utf-8")))


def test_successful_parent_cannot_leave_background_descendants(tmp_path: Path):
    pid_file = tmp_path / "background-child.pid"

    result = run_command(
        _spawn_child_command(pid_file, parent_sleeps=False),
        cwd=tmp_path,
        env=None,
        capture=False,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert pid_file.is_file()
    _assert_process_stopped(int(pid_file.read_text(encoding="utf-8")))


def test_command_timeout_validation_rejects_boolean_and_non_positive_values(tmp_path: Path):
    command = [sys.executable, "--version"]
    for timeout in (True, 0, -1):
        with pytest.raises(ValueError, match="positive integer"):
            run_command(command, cwd=tmp_path, env=None, capture=False, timeout_seconds=timeout)
