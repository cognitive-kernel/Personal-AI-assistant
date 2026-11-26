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

from app.autonomous_engineering_execution_lock import repository_execution_lock
from app.autonomous_engineering_state import execution_state_directory


def test_execution_lock_records_owner_metadata_and_releases_after_scope(
    monkeypatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_EXECUTION_OWNER_TOKEN", raising=False)
    with repository_execution_lock("run-1", "attempt-2"):
        assert len(os.environ["AUTONOMOUS_ENGINEERING_EXECUTION_OWNER_TOKEN"]) == 64

    assert "AUTONOMOUS_ENGINEERING_EXECUTION_OWNER_TOKEN" not in os.environ
    # Windows byte-range locks also prevent a second handle from reading the
    # locked file; inspect its diagnostic metadata after ownership is released.
    import json

    lock_bytes = (shared_state_tmp_path / "repository-execution.lock").read_bytes()
    assert lock_bytes[:1] == b"0"
    metadata = json.loads(lock_bytes[1:].decode("utf-8"))
    assert metadata["schema_version"] == 2
    assert metadata["pid"] == os.getpid()
    assert metadata["process_started_at"] == pytest.approx(psutil.Process(os.getpid()).create_time(), abs=0.02)
    assert len(metadata["owner_token"]) == 64
    assert all(character in "0123456789abcdef" for character in metadata["owner_token"])
    assert metadata["run_id"] == "run-1"
    assert metadata["run_attempt"] == "attempt-2"
    assert metadata["host"]
    assert metadata["acquired_at"] > 0

    with repository_execution_lock("run-2", "attempt-1"):
        pass


def test_execution_lock_is_released_by_operating_system_after_process_crash(
    monkeypatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path))
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


def test_execution_state_directory_uses_configured_shared_root(monkeypatch, shared_state_tmp_path: Path):
    configured = shared_state_tmp_path
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(configured))
    assert execution_state_directory() == configured


def test_cycle_orchestrator_preserves_public_lock_api_via_module_boundary():
    from scripts import autonomous_engineering_cycle as cycle

    assert cycle.repository_execution_lock is repository_execution_lock


def test_progress_checkpoint_and_controller_share_expanded_state_root(
    monkeypatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    from scripts import autonomous_engineering_checkpoint as checkpoint
    from scripts import autonomous_engineering_cycle as cycle
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_STATE_TEST_ROOT", str(shared_state_tmp_path / "resolved-state"))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "$AUTONOMOUS_STATE_TEST_ROOT")

    expected = shared_state_tmp_path / "resolved-state"
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
    wrapper_pid_file = tmp_path / "codex-supervisor.pid"
    child_pid_file = tmp_path / "orphaned-codex-child.pid"
    child_code = "import time; time.sleep(60)"
    codex_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "open(" + repr(str(child_pid_file)) + ",'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    runner_path = str(Path(codex_runner.__file__).resolve())
    workspace = str(Path(__file__).resolve().parents[1])
    owner_code = "\n".join([
        "import json, os, subprocess, sys, time",
        "from pathlib import Path",
        "import psutil",
        "owner_chain = json.dumps([{'pid': os.getpid(), 'started_at': float(psutil.Process(os.getpid()).create_time())}])",
        "wrapper = subprocess.Popen([sys.executable, " + repr(runner_path) + ", '--timeout-seconds', '30', '--parent-chain', owner_chain, '--', sys.executable, '-c', " + repr(codex_code) + "], cwd=" + repr(workspace) + ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        "Path(" + repr(str(wrapper_pid_file)) + ").write_text(str(wrapper.pid))",
        "time.sleep(60)",
    ])
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code],
        cwd=workspace,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wrapper_pid = None
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not wrapper_pid_file.is_file():
            if owner.poll() is not None:
                raise AssertionError("The owner exited before launching the Codex supervisor.")
            time.sleep(0.05)
        assert wrapper_pid_file.is_file(), "The owner did not launch the Codex supervisor."
        wrapper_pid = int(wrapper_pid_file.read_text(encoding="utf-8"))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not child_pid_file.is_file():
            if not psutil.pid_exists(wrapper_pid):
                raise AssertionError("Codex supervisor exited before launching its child.")
            time.sleep(0.05)
        assert child_pid_file.is_file(), "Codex child did not start under the owner-held job."
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        psutil.Process(wrapper_pid).kill()
        psutil.Process(wrapper_pid).wait(timeout=5)
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
        if wrapper_pid and psutil.pid_exists(wrapper_pid):
            try:
                psutil.Process(wrapper_pid).kill()
            except psutil.NoSuchProcess:
                pass
        if child_pid and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.NoSuchProcess:
                pass

@pytest.mark.skipif(os.name != "nt", reason="Windows controller-owned Job Object contract.")
def test_windows_progress_owner_kills_codex_descendants_after_supervisor_crash(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    from scripts import autonomous_engineering_progress as progress

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Run the task", encoding="utf-8")
    pid_file = tmp_path / "orphaned-codex-child.pid"
    supervisor = tmp_path / "fake-codex-supervisor.py"
    supervisor.write_text(
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "Path(" + repr(str(pid_file)) + ").write_text(str(child.pid))\n"
        "os._exit(7)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(progress.shutil, "which", lambda _: "codex.exe")
    monkeypatch.setattr(progress, "codex_arguments", lambda model, report: [])
    monkeypatch.setattr(
        progress,
        "codex_command",
        lambda executable, arguments, **kwargs: [sys.executable, str(supervisor)],
    )
    monkeypatch.setattr(progress, "persist", lambda record: True)
    monkeypatch.setattr(progress, "plan_state", lambda: (0, 0, None))
    monkeypatch.setattr(progress, "PLAN_FILE", tmp_path / ".agent-progress.json")

    args = SimpleNamespace(
        model="gpt-oss:20b",
        prompt=str(prompt),
        report=str(tmp_path / "report.md"),
        run_id="run-owner-test",
        run_attempt="1",
    )

    assert progress.run_codex(args) == 7
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        child = psutil.Process(child_pid)
        assert child.status() == psutil.STATUS_ZOMBIE or not child.is_running(), (
            "A Codex descendant survived supervisor exit while the progress owner was still alive."
        )
    except psutil.NoSuchProcess:
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX controller-owned process-group contract.")
def test_posix_progress_owner_kills_codex_descendants_after_supervisor_crash(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    from scripts import autonomous_engineering_progress as progress

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Run the task", encoding="utf-8")
    pid_file = tmp_path / "orphaned-codex-child.pid"
    supervisor = tmp_path / "fake-codex-supervisor.py"
    supervisor.write_text(
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "Path(" + repr(str(pid_file)) + ").write_text(str(child.pid))\n"
        "os._exit(7)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(progress.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(progress, "codex_arguments", lambda model, report: [])
    monkeypatch.setattr(
        progress,
        "codex_command",
        lambda executable, arguments, **kwargs: [sys.executable, str(supervisor)],
    )
    monkeypatch.setattr(progress, "persist", lambda record: True)
    monkeypatch.setattr(progress, "plan_state", lambda: (0, 0, None))
    monkeypatch.setattr(progress, "PLAN_FILE", tmp_path / ".agent-progress.json")

    args = SimpleNamespace(
        model="gpt-oss:20b",
        prompt=str(prompt),
        report=str(tmp_path / "report.md"),
        run_id="run-owner-test",
        run_attempt="1",
    )

    assert progress.run_codex(args) == 7
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        child = psutil.Process(child_pid)
        assert child.status() == psutil.STATUS_ZOMBIE or not child.is_running(), (
            "A Codex descendant survived supervisor exit while the progress owner was still alive."
        )
    except psutil.NoSuchProcess:
        pass

def test_publish_parent_guard_requires_the_locked_cycle_controller(
    monkeypatch, shared_state_tmp_path: Path,
):
    from app.autonomous_engineering_execution_lock import require_parent_repository_execution_lock

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path))
    with pytest.raises(RuntimeError, match="must run as a child"):
        require_parent_repository_execution_lock("run-guard-test")

    code = (
        "from app.autonomous_engineering_execution_lock import "
        "require_parent_repository_execution_lock; "
        "require_parent_repository_execution_lock('run-guard-test')"
    )
    with repository_execution_lock("run-guard-test", "1"):
        child = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    assert child.returncode == 0, child.stderr

    second_code = code.replace("run-guard-test", "second-run")
    wrong_run = second_code.replace("second-run", "different-run")
    with repository_execution_lock("second-run", "2"):
        child = subprocess.run(
            [sys.executable, "-c", second_code],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert child.returncode == 0, child.stderr
        child = subprocess.run(
            [sys.executable, "-c", wrong_run],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    assert child.returncode != 0
    assert "not owned by the active cycle controller" in child.stderr
