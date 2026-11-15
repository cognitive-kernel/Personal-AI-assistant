from __future__ import annotations

import json
import subprocess

import pytest
from fastapi import HTTPException

from app import autonomous_engineering as agent
from app import autonomous_engineering_progress as progress


def _ready(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True,
        "paused": False,
        "github": {"runs_available": True, "recent_runs": []},
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")


def test_progress_reader_uses_the_runner_machine_aware_state_root(monkeypatch, tmp_path):
    expected = tmp_path / "machine-shared-state"
    monkeypatch.setattr(progress, "execution_state_directory", lambda: expected)

    assert progress._progress_dir() == expected


def test_pending_work_status_reports_only_marker_presence_without_reading_contents(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "_progress_dir", lambda: tmp_path)
    (tmp_path / "pending-manual-task.txt").write_text("private task contents", encoding="utf-8")
    (tmp_path / "pending-manual-task-owner.json").write_text('{"private":"owner"}', encoding="utf-8")
    (tmp_path / "pending-autonomous-pr.json").write_text('{"number":123}', encoding="utf-8")

    result = progress.pending_work_status()

    assert result == {
        "available": True,
        "blocked": True,
        "markers": {
            "pending_task": True,
            "pending_owner": True,
            "pending_pr": True,
            "cleanup_in_progress": False,
        },
        "reason": "pending_task, pending_owner, pending_pr",
    }
    assert "private task contents" not in repr(result)
    assert "private" not in repr(result)


def test_pending_work_status_fails_closed_when_shared_lock_cannot_be_acquired(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "_progress_dir", lambda: tmp_path)

    class LockFailure:
        def __init__(self, *args, **kwargs):
            raise TimeoutError("busy")

    monkeypatch.setattr(progress, "durable_state_lock", LockFailure)

    result = progress.pending_work_status()

    assert result["available"] is False
    assert result["blocked"] is True
    assert result["markers"] == {}


def test_manual_dispatch_is_rejected_when_a_durable_task_is_pending(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": True,
        "blocked": True,
        "markers": {"pending_task": True},
    })
    calls = []
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: calls.append(command))

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="new task"))

    assert error.value.status_code == 409
    assert "تعیین‌تکلیف نشده" in error.value.detail
    assert calls == []


def test_manual_dispatch_is_rejected_when_shared_state_cannot_be_verified(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": False,
        "blocked": True,
        "markers": {},
    })

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="new task"))

    assert error.value.status_code == 503


def test_manual_dispatch_is_rejected_when_an_autonomous_pr_is_open(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": True,
        "blocked": False,
        "markers": {},
    })
    calls = []

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1] == "api":
            pulls = [{
                "state": "open",
                "user": {"login": "github-actions[bot]"},
                "head": {
                    "ref": "codex/local-engineering-123-1-task-1",
                    "repo": {"full_name": "cognitive-kernel/Personal-AI-assistant"},
                },
                "base": {"ref": "main"},
            }]
            return subprocess.CompletedProcess(command, 0, json.dumps(pulls), "")
        raise AssertionError(f"Workflow dispatch must not occur: {command!r}")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="new task"))

    assert error.value.status_code == 409
    assert "PR مهندسی خودکار" in error.value.detail
    assert len(calls) == 2
    assert calls[1][1] == "api"


def test_manual_dispatch_fails_closed_when_github_cannot_confirm_open_pr_state(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": True,
        "blocked": False,
        "markers": {},
    })

    def fake_run(command, timeout=8):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "network unavailable")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="new task"))

    assert error.value.status_code == 503


def test_packaged_state_resolver_prefers_machine_scope(monkeypatch, tmp_path):
    from app import autonomous_engineering_state as state

    machine_root = tmp_path / "machine-state"
    monkeypatch.setattr(state, "_is_windows", lambda: True)
    monkeypatch.setattr(state, "_machine_configured_state_directory", lambda: str(machine_root))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)

    assert state.execution_state_directory() == machine_root


def test_packaged_state_resolver_rejects_a_divergent_process_override(monkeypatch, tmp_path):
    from app import autonomous_engineering_state as state

    monkeypatch.setattr(state, "_is_windows", lambda: True)
    monkeypatch.setattr(state, "_machine_configured_state_directory", lambda: str(tmp_path / "machine"))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path / "user"))

    with pytest.raises(RuntimeError, match="differs from the machine-scoped state root"):
        state.execution_state_directory()


def test_storage_accessible_checks_the_shared_state_directory(monkeypatch, tmp_path):
    from app import autonomous_engineering_progress as progress

    monkeypatch.setattr(progress, "_progress_dir", lambda: tmp_path)
    assert progress.storage_accessible() is True

    missing = tmp_path / "missing"
    monkeypatch.setattr(progress, "_progress_dir", lambda: missing)
    assert progress.storage_accessible() is False


def test_script_state_facades_import_before_project_install_and_outside_repository_cwd(tmp_path):
    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parents[1]
    paths_script = root / "scripts" / "autonomous_engineering_state_paths.py"
    lock_script = root / "scripts" / "autonomous_engineering_state_lock.py"
    file_lock_script = root / "scripts" / "autonomous_engineering_file_lock.py"
    code = (
        "import runpy; "
        f"runpy.run_path({str(paths_script)!r}); "
        f"runpy.run_path({str(lock_script)!r}); "
        f"runpy.run_path({str(file_lock_script)!r}); "
        "print('shared-state-facades-imported')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "shared-state-facades-imported" in result.stdout


def test_dashboard_and_runner_use_the_same_durable_state_lock(tmp_path):
    import sys
    import time
    from pathlib import Path

    from scripts.autonomous_engineering_state_lock import durable_state_lock as runner_lock

    root = Path(__file__).resolve().parents[1]
    ready = tmp_path / "app-lock-ready.txt"
    code = (
        "import time\n"
        "from pathlib import Path\n"
        "from app.autonomous_engineering_state import durable_state_lock\n"
        f"with durable_state_lock(Path({str(tmp_path)!r})):\n"
        f"    Path({str(ready)!r}).write_text('ready', encoding='utf-8')\n"
        "    time.sleep(1.0)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.is_file():
            if child.poll() is not None:
                output = child.stdout.read() if child.stdout else ""
                pytest.fail(f"Dashboard lock owner exited early: {output}")
            time.sleep(0.02)
        assert ready.is_file(), "Dashboard did not acquire the shared durable-state lock."
        with pytest.raises(TimeoutError):
            with runner_lock(tmp_path, timeout_seconds=0.05):
                pytest.fail("The Runner must contend on the dashboard's exact durable-state lock.")
    finally:
        child.wait(timeout=10)

    with runner_lock(tmp_path, timeout_seconds=1):
        pass


def test_shared_file_lock_rejects_non_finite_timeout(tmp_path):
    from app.autonomous_engineering_state import exclusive_file_lock

    with pytest.raises(ValueError, match="finite non-negative"):
        with exclusive_file_lock(
            tmp_path / "invalid-timeout.lock",
            timeout_seconds=float("nan"),
            timeout_message="unreachable",
        ):
            pytest.fail("A non-finite lock timeout must be rejected before acquisition.")


def test_empty_dispatch_remains_available_to_resume_persisted_work(monkeypatch):
    _ready(monkeypatch)

    def unexpected_pending_check():
        pytest.fail("An empty resume dispatch must not be treated as a new manual task.")

    monkeypatch.setattr(agent, "pending_work_status", unexpected_pending_check)
    calls = []

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["workflow", "run"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"Unexpected command: {command!r}")

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent.dispatch_autonomous_engineering()

    assert result["accepted"] is True
    workflow_call = next(command for command in calls if command[1:3] == ["workflow", "run"])
    assert "--field" in workflow_call
    assert "manual_task=" in workflow_call
