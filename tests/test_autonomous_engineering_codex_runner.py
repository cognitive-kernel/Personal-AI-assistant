from __future__ import annotations

import os
import subprocess
import sys
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


def test_parent_chain_rejects_pid_reuse_or_missing_process():
    assert runner.parent_chain_is_alive(runner.capture_parent_chain())
    assert not runner.parent_chain_is_alive([{"pid": 2_000_000_000, "started_at": 1.0}])


def test_windows_process_job_is_a_noop_on_non_windows_hosts():
    if os.name == "nt":
        return
    with runner.WindowsProcessJob() as job:
        assert job._handle is None
