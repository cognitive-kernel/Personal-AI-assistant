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



@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object owner-close contract.")
def test_windows_owner_exit_terminates_command_and_descendants(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[1]
    pid_file = tmp_path / "owner-exit-child.pid"
    owner_script = tmp_path / "command-owner.py"
    owner_script.write_text(
        f"""
import sys
import time
from pathlib import Path
from scripts.autonomous_engineering_command_runner import run_command

pid_file = Path({str(pid_file)!r})
child_code = "import time; time.sleep(60)"
parent_code = (
    "import subprocess,sys,time; "
    "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
    "open(" + repr(str(pid_file)) + ",'w').write(str(child.pid)); "
    "time.sleep(60)"
)
run_command(
    [sys.executable, "-c", parent_code],
    cwd=Path({str(repository_root)!r}),
    env=None,
    capture=False,
    timeout_seconds=60,
)
""",
        encoding="utf-8",
    )

    owner = subprocess.Popen([sys.executable, str(owner_script)], cwd=repository_root)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not pid_file.is_file():
            if owner.poll() is not None:
                raise AssertionError("Command owner exited before starting its descendant.")
            time.sleep(0.05)
        assert pid_file.is_file(), "Command descendant did not start."
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        owner.kill()
        owner.wait(timeout=5)
        _assert_process_stopped(child_pid)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
