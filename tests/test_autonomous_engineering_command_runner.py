from __future__ import annotations

import os
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


def test_binary_command_input_is_preserved_byte_for_byte(tmp_path: Path):
    payload = b"checkpoint\x00patch\r\nwith\xffbytes\n"
    output_path = tmp_path / "received-patch.bin"
    script = (
        "import pathlib,sys; "
        "pathlib.Path(" + repr(str(output_path)) + ").write_bytes(sys.stdin.buffer.read())"
    )

    result = run_command(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=None,
        capture=True,
        timeout_seconds=5,
        input_data=payload,
    )

    assert result.returncode == 0
    assert output_path.read_bytes() == payload


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

    owner_env = os.environ.copy()
    existing_pythonpath = owner_env.get("PYTHONPATH", "")
    owner_env["PYTHONPATH"] = (
        str(repository_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    )
    owner = subprocess.Popen(
        [sys.executable, str(owner_script)], cwd=repository_root, env=owner_env,
    )
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group termination contract.")
def test_posix_command_waits_for_descendant_termination_before_return(tmp_path: Path):
    marker = tmp_path / "child-terminated.marker"
    ready = tmp_path / "child-handler-ready.marker"
    pid_file = tmp_path / "child.pid"
    child_code = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: Path("
        + repr(str(marker))
        + ").write_text('terminated')); "
        "Path(" + repr(str(ready)) + ").write_text('ready'); time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "])\n"
        "deadline=time.monotonic()+5\n"
        "ready=Path(" + repr(str(ready)) + ")\n"
        "while not ready.exists() and time.monotonic()<deadline:\n"
        "    time.sleep(0.01)\n"
        "assert ready.exists(), 'child signal handler did not initialize'\n"
        "open(" + repr(str(pid_file)) + ",'w').write(str(child.pid))\n"
    )

    result = run_command(
        [sys.executable, "-c", parent_code],
        cwd=tmp_path,
        env=None,
        capture=False,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert pid_file.is_file()
    assert marker.read_text(encoding="utf-8") == "terminated"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object drain contract.")
def test_windows_command_waits_until_job_is_empty_before_return(tmp_path: Path):
    pid_file = tmp_path / "windows-child.pid"
    result = run_command(
        _spawn_child_command(pid_file, parent_sleeps=False),
        cwd=tmp_path,
        env=None,
        capture=False,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(child_pid), "A Job Object descendant remained alive after run_command returned."


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended-process cleanup contract.")
def test_windows_assignment_failure_does_not_leave_suspended_process(monkeypatch, tmp_path: Path):
    spawned: dict[str, int] = {}

    def fail_assignment(self, process):
        spawned["pid"] = int(process.pid)
        raise OSError("simulated Job Object assignment failure")

    monkeypatch.setattr(
        "scripts.autonomous_engineering_command_runner.WindowsProcessJob.assign_and_resume",
        fail_assignment,
    )
    with pytest.raises(OSError, match="assignment failure"):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            env=None,
            capture=False,
            timeout_seconds=5,
        )

    assert "pid" in spawned
    assert not psutil.pid_exists(spawned["pid"]), "An unassigned suspended process survived fail-closed cleanup."


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group API validation.")
def test_posix_process_group_wait_rejects_invalid_budgets():
    from scripts.autonomous_engineering_process_group import (
        terminate_process_group,
        wait_for_process_group_exit,
    )

    for value in (True, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            wait_for_process_group_exit(os.getpgrp(), value)
    for value in (True, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            terminate_process_group(os.getpgrp(), grace_seconds=value)
