from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from scripts.autonomous_engineering_process_job import WindowsProcessJob

from scripts.autonomous_engineering_execution_lock import (
    execution_state_directory,
    repository_execution_lock,
)


def test_execution_lock_records_owner_metadata_and_releases_after_scope(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
    with repository_execution_lock("run-1", "attempt-2"):
        pass

    # Windows byte-range locks also prevent a second handle from reading the
    # locked file; inspect its diagnostic metadata after ownership is released.
    import json

    metadata = json.loads((tmp_path / "repository-execution.lock").read_text(encoding="utf-8"))
    assert metadata["pid"] == os.getpid()
    assert metadata["run_id"] == "run-1"
    assert metadata["run_attempt"] == "attempt-2"
    assert metadata["host"]
    assert metadata["acquired_at"] > 0

    with repository_execution_lock("run-2", "attempt-1"):
        pass


def test_execution_lock_is_released_by_operating_system_after_process_crash(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
    code = (
        "import os; "
        "from scripts.autonomous_engineering_execution_lock import repository_execution_lock; "
        "lock=repository_execution_lock('crashed-run','1'); "
        "lock.__enter__(); os._exit(23)"
    )
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert child.returncode == 23

    with repository_execution_lock("recovery-run", "1"):
        pass


def test_execution_state_directory_uses_configured_shared_root(monkeypatch, tmp_path: Path):
    configured = tmp_path / "shared-state"
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(configured))
    assert execution_state_directory() == configured


def test_cycle_orchestrator_preserves_public_lock_api_via_module_boundary():
    from scripts import autonomous_engineering_cycle as cycle

    assert cycle.repository_execution_lock is repository_execution_lock


def test_progress_checkpoint_and_controller_share_expanded_state_root(
    monkeypatch, tmp_path: Path,
):
    from scripts import autonomous_engineering_checkpoint as checkpoint
    from scripts import autonomous_engineering_cycle as cycle
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_STATE_TEST_ROOT", str(tmp_path / "resolved-state"))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "$AUTONOMOUS_STATE_TEST_ROOT")

    expected = tmp_path / "resolved-state"
    assert execution_state_directory() == expected
    assert progress.store_dir() == expected
    assert checkpoint.store_dir() == expected
    assert cycle._autonomous_state_dir() == expected



@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership is Windows-only")
def test_windows_codex_job_kills_child_and_grandchild_when_owner_closes(tmp_path: Path):
    grandchild_pid_file = tmp_path / "grandchild.pid"
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(grandchild_code) + "]); "
        "open(" + repr(str(grandchild_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )

    with WindowsProcessJob() as job:
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            creationflags=WindowsProcessJob.CREATE_SUSPENDED,
        )
        job.assign_and_resume(child)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not grandchild_pid_file.is_file():
            if child.poll() is not None:
                raise AssertionError("Job-owned Codex child exited before spawning its descendant.")
            time.sleep(0.05)
        assert grandchild_pid_file.is_file(), "Job-owned Codex child did not create its descendant."

    child.wait(timeout=5)
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8"))
    try:
        psutil.Process(grandchild_pid).wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        raise AssertionError("A Codex descendant survived closure of its owning Windows Job Object.")
