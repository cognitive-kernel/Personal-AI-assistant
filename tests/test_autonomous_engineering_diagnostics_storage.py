from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from fastapi import HTTPException

from app import autonomous_engineering as agent
from app import autonomous_engineering_storage as storage


def test_storage_inventory_lists_runner_temp_files_and_state_temps(monkeypatch, tmp_path):
    state = tmp_path / "state"
    runner_temp = tmp_path / "runner" / "_work" / "_temp"
    state.mkdir()
    runner_temp.mkdir(parents=True)
    (state / "current.json.tmp").write_text("state", encoding="utf-8")
    (state / "events.jsonl").write_text("event", encoding="utf-8")
    (runner_temp / "codex-report-7.md").write_text("report", encoding="utf-8")
    (runner_temp / "unrelated-user-file.tmp").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(storage, "execution_state_directory", lambda: state)
    monkeypatch.setattr(storage, "_runner_temp_directories", lambda: [runner_temp])

    result = storage.inspect_storage()

    names = {item["name"] for item in result["files"]}
    assert "current.json.tmp" in names
    assert "events.jsonl" in names
    assert "codex-report-7.md" in names
    assert "unrelated-user-file.tmp" in names
    assert result["file_count"] == 4
    assert next(item for item in result["files"] if item["name"] == "codex-report-7.md")["cleanup_eligible"] is False
    assert next(item for item in result["files"] if item["name"] == "events.jsonl")["cleanup_eligible"] is False


def test_cleanup_removes_stale_temp_and_only_old_runner_diagnostic_logs(monkeypatch, tmp_path):
    state = tmp_path / "state"
    runner_temp = tmp_path / "runner" / "_work" / "_temp"
    state.mkdir()
    runner_temp.mkdir(parents=True)
    diagnostic_dir = tmp_path / "runner" / "_diag"
    diagnostic_dir.mkdir()
    stale_state = state / "stale.tmp"
    stale_codex = runner_temp / "codex-report-9.md"
    protected_checkpoint = state / "checkpoint.json"
    unrelated = runner_temp / "unrelated.tmp"
    old_diagnostic = diagnostic_dir / "Runner_2020-01-01.log"
    recent_diagnostic = diagnostic_dir / "Runner_recent.log"
    for path in (stale_state, stale_codex, protected_checkpoint, unrelated):
        path.write_text("keep-or-delete", encoding="utf-8")
        os.utime(path, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    old_diagnostic.write_text("old log", encoding="utf-8")
    os.utime(old_diagnostic, (time.time() - 40 * 24 * 3600, time.time() - 40 * 24 * 3600))
    recent_diagnostic.write_text("recent log", encoding="utf-8")
    os.utime(recent_diagnostic, (time.time() - 10 * 24 * 3600, time.time() - 10 * 24 * 3600))
    monkeypatch.setattr(storage, "execution_state_directory", lambda: state)
    monkeypatch.setattr(storage, "_runner_temp_directories", lambda: [runner_temp])
    monkeypatch.setattr(storage, "_runner_worker_active", lambda: False)

    result = storage.cleanup_storage(active_run=False)

    assert result["deleted_count"] == 4
    assert not stale_state.exists()
    assert not stale_codex.exists()
    assert protected_checkpoint.exists()
    assert not unrelated.exists()
    assert not old_diagnostic.exists()
    assert recent_diagnostic.exists()


def test_cleanup_is_blocked_while_a_run_is_active(tmp_path):
    result = storage.cleanup_storage(active_run=True)
    assert result["blocked"] is True
    assert result["deleted"] == []


def test_cancel_targets_the_selected_active_run(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps([
                {"databaseId": 101, "status": "in_progress"},
                {"databaseId": 202, "status": "queued"},
            ]), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.cancel_autonomous_engineering(agent.RunControlRequest(run_id="202"))

    assert result["run_id"] == "202"
    assert calls[-1][1:4] == ["run", "cancel", "202"]


def test_failed_github_job_exposes_failed_step_in_progress(monkeypatch):
    agent._RUN_PROGRESS_CACHE.update({"run_id": None, "at": 0.0, "value": None})
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    jobs = {"jobs": [{
        "name": "codex",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/123/job/456",
        "steps": [
            {"name": "Checkout", "status": "completed", "conclusion": "success"},
            {"name": "Execute autonomous task", "status": "completed", "conclusion": "failure"},
        ],
    }]}
    monkeypatch.setattr(
        agent, "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(jobs), ""),
    )

    result = agent._enrich_progress_from_github({"run_id": "123", "status": "running"})

    assert result["status"] == "failed"
    assert result["diagnostic"]["job"] == "codex"
    assert result["diagnostic"]["step"] == "Execute autonomous task"
    assert result["diagnostic"]["job_url"].endswith("/job/456")



def test_record_failure_persists_relative_source_location(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setattr(progress, "store_dir", lambda: tmp_path)
    assert progress.record_failure(
        "987", "1", "RuntimeError", "test failure",
        "scripts/autonomous_engineering_cycle.py", 321, "run_cycle",
    )
    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert current["status"] == "failed"
    assert current["diagnostic"]["source_file"] == "scripts/autonomous_engineering_cycle.py"
    assert current["diagnostic"]["line"] == 321
    assert current["diagnostic"]["function"] == "run_cycle"


def test_delete_run_refuses_active_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["api", "repos/cognitive-kernel/Personal-AI-assistant/actions/runs/456"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"status": "in_progress", "path": ".github/workflows/autonomous-codex-engineering.yml"}), "")
        raise AssertionError("Active run must never be deleted.")

    monkeypatch.setattr(agent, "_run", fake_run)
    with pytest.raises(HTTPException) as error:
        agent.delete_autonomous_engineering_run("456")

    assert error.value.status_code == 409
    assert all(command[1:3] != ["run", "delete"] for command in calls)


def test_delete_run_removes_only_a_confirmed_completed_run(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["api", "repos/cognitive-kernel/Personal-AI-assistant/actions/runs/789"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"status": "completed", "conclusion": "success", "path": ".github/workflows/autonomous-codex-engineering.yml"}), "")
        if command[1:3] == ["run", "delete"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.delete_autonomous_engineering_run("789")

    assert result["deleted"] is True
    assert calls[-1][1:4] == ["run", "delete", "789"]
