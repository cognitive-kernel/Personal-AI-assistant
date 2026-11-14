from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from scripts import autonomous_engineering_codex_runner as runner


def test_codex_refuses_to_start_when_any_owner_process_identity_is_gone(tmp_path: Path):
    marker = tmp_path / "must-not-exist"
    command = [
        sys.executable, "-c",
        "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('started')",
    ]

    result = runner.run(command, 10, [{"pid": 2_000_000_000, "started_at": 1.0}])

    assert result == 125
    assert not marker.exists()


def test_codex_runner_enforces_a_hard_deadline_and_reaps_descendants(tmp_path: Path):
    child_pid_file = tmp_path / "child.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )

    result = runner.run(
        [sys.executable, "-c", parent_code],
        1,
        runner.capture_parent_chain(),
    )

    assert result == 124
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    try:
        psutil.Process(child_pid).wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        raise AssertionError("Codex descendant survived its owning supervisor timeout.")


def test_parent_chain_rejects_pid_reuse_or_missing_process(monkeypatch):
    assert runner.parent_chain_is_alive(runner.capture_parent_chain())
    assert not runner.parent_chain_is_alive([{"pid": 2_000_000_000, "started_at": 1.0}])

    class ReusedPid:
        def is_running(self):
            return True

        def create_time(self):
            return 9999.0

    monkeypatch.setattr(runner.psutil, "Process", lambda pid: ReusedPid())
    assert not runner.parent_chain_is_alive([{"pid": 42, "started_at": 1.0}])


def test_codex_runner_terminates_descendants_when_owner_process_exits(tmp_path: Path):
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child_pid_file = tmp_path / "orphan-child.pid"
    child_code = "import time; time.sleep(60)"
    command_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    chain = [{"pid": owner.pid, "started_at": float(psutil.Process(owner.pid).create_time())}]
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(Path(runner.__file__).resolve()),
            "--timeout-seconds", "30",
            "--parent-chain", json.dumps(chain),
            "--", sys.executable, "-c", command_code,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while __import__("time").monotonic() < deadline and not child_pid_file.is_file():
            if wrapper.poll() is not None:
                raise AssertionError("Codex wrapper exited before launching the child.")
            time.sleep(0.05)
        assert child_pid_file.is_file(), "Codex child did not start."
        owner.terminate()
        owner.wait(timeout=5)
        assert wrapper.wait(timeout=10) == 125
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        try:
            psutil.Process(child_pid).wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            raise AssertionError("Codex descendant survived its owner process.")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)


def test_windows_process_job_is_a_noop_on_non_windows_hosts():
    if os.name == "nt":
        return
    with runner.WindowsProcessJob() as job:
        assert job._handle is None


def test_parent_managed_posix_group_is_inherited_and_drained_by_owner(tmp_path: Path):
    child_pid_file = tmp_path / "managed-group-child.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(child.pid))"
    )
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(Path(runner.__file__).resolve()),
            "--timeout-seconds", "10",
            "--parent-chain", json.dumps(runner.capture_parent_chain()),
            "--parent-group-managed",
            "--", sys.executable, "-c", parent_code,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert wrapper.wait(timeout=10) == 0
        assert child_pid_file.is_file()
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert psutil.pid_exists(child_pid), "The external owner group was not retained for owner cleanup."
        from scripts.autonomous_engineering_process_group import terminate_process_group
        assert terminate_process_group(wrapper.pid)
        try:
            child = psutil.Process(child_pid)
            assert child.status() == psutil.STATUS_ZOMBIE or not child.is_running(), (
                "The owner could not drain the inherited POSIX process group."
            )
        except psutil.NoSuchProcess:
            pass
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX broken-pipe owner-loss contract.")
def test_codex_runner_kills_inherited_group_when_output_owner_disappears(tmp_path: Path):
    child_pid_file = tmp_path / "broken-pipe-child.pid"
    owner_script = tmp_path / "output-owner.py"
    runner_path = Path(runner.__file__).resolve()
    command_code = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(child.pid)); "
        "sys.stdout.write('x'*8000000); sys.stdout.flush()"
    )
    owner_script.write_text(
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "from scripts.autonomous_engineering_codex_runner import capture_parent_chain\n"
        "command = [sys.executable, " + repr(str(runner_path)) + ", "
        "'--timeout-seconds', '30', '--parent-chain', "
        "json.dumps(capture_parent_chain()), '--parent-group-managed', '--', "
        "sys.executable, '-c', " + repr(command_code) + "]\n"
        "runner_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)\n"
        "deadline = time.monotonic() + 10\n"
        "while not Path(" + repr(str(child_pid_file)) + ").exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "assert Path(" + repr(str(child_pid_file)) + ").exists(), 'Codex child did not start'\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    owner = subprocess.Popen(
        [sys.executable, str(owner_script)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert owner.wait(timeout=15) == 0
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            child = psutil.Process(child_pid)
            if child.status() == psutil.STATUS_ZOMBIE or not child.is_running():
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.05)
    raise AssertionError("Codex descendant survived after the output owner disappeared.")
