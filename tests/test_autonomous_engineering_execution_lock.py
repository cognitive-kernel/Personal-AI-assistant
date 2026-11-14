from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from scripts.autonomous_engineering_process_job import WindowsProcessJob
from scripts import autonomous_engineering_codex_runner as codex_runner

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



@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership is Windows-only")
def test_windows_codex_supervisor_job_handle_is_pinned_in_owner(tmp_path: Path):
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child_pid_file = tmp_path / "codex-child.pid"
    child_code = "import time; time.sleep(60)"
    codex_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    owner_chain = [{
        "pid": owner.pid,
        "started_at": float(psutil.Process(owner.pid).create_time()),
    }]
    wrapper = subprocess.Popen(
        [
            sys.executable, str(Path(codex_runner.__file__).resolve()),
            "--timeout-seconds", "30",
            "--parent-chain", json.dumps(owner_chain),
            "--", sys.executable, "-c", codex_code,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not child_pid_file.is_file():
            if wrapper.poll() is not None:
                raise AssertionError("Codex supervisor exited before launching its child.")
            time.sleep(0.05)
        assert child_pid_file.is_file(), "Codex child did not start under the owner-bound job."
        owner.terminate()
        owner.wait(timeout=5)
        assert wrapper.wait(timeout=10) == 125
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        try:
            psutil.Process(child_pid).wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            raise AssertionError("Codex child survived owner loss despite the duplicated Job Object handle.")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)



@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership is Windows-only")
def test_windows_owner_handle_reaps_codex_descendants_after_supervisor_crash(tmp_path: Path):
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child_pid_file = tmp_path / "orphaned-codex-child.pid"
    child_code = "import time; time.sleep(60)"
    codex_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    owner_chain = [{
        "pid": owner.pid,
        "started_at": float(psutil.Process(owner.pid).create_time()),
    }]
    wrapper = subprocess.Popen(
        [
            sys.executable, str(Path(codex_runner.__file__).resolve()),
            "--timeout-seconds", "30",
            "--parent-chain", json.dumps(owner_chain),
            "--", sys.executable, "-c", codex_code,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not child_pid_file.is_file():
            if wrapper.poll() is not None:
                raise AssertionError("Codex supervisor exited before launching its child.")
            time.sleep(0.05)
        assert child_pid_file.is_file(), "Codex child did not start under the owner-held job."
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        wrapper.kill()
        wrapper.wait(timeout=5)
        assert psutil.Process(child_pid).is_running(), "Owner-held Job Object did not survive supervisor crash."
        owner.terminate()
        owner.wait(timeout=5)
        try:
            psutil.Process(child_pid).wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            raise AssertionError("Owner exit failed to reap Codex descendants after supervisor crash.")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)
