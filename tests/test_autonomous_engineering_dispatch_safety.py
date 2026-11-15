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
