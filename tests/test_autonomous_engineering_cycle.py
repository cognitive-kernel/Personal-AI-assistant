from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import autonomous_engineering_cycle as cycle


@pytest.mark.parametrize(
    ("report", "expected"),
    [("TASK_COMPLETE", "TASK_COMPLETE"), ("QUEUE_EMPTY", "QUEUE_EMPTY"), ("TASK_BLOCKED", "TASK_BLOCKED")],
)
def test_task_marker_accepts_exactly_one_marker(tmp_path: Path, report: str, expected: str):
    path = tmp_path / "report.md"
    path.write_text(f"Task result\n{report}\n", encoding="utf-8")
    assert cycle.task_marker(path) == expected


@pytest.mark.parametrize("report", ["", "TASK_COMPLETE\nQUEUE_EMPTY", "TASK_UNKNOWN", "TASK_COMPLETE\nTASK_COMPLETE"])
def test_task_marker_rejects_missing_or_ambiguous_marker(tmp_path: Path, report: str):
    path = tmp_path / "report.md"
    path.write_text(report, encoding="utf-8")
    with pytest.raises(RuntimeError):
        cycle.task_marker(path)


def test_validate_paths_accepts_only_reviewable_project_paths():
    cycle.validate_paths(["app/example.py", "tests/test_example.py", "docs/example.md", "scripts/example.py"])


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        ".github/workflows/autonomous-codex-engineering.yml",
        "scripts/autonomous_engineering_cycle.py",
        "secrets/token.txt",
        "app/../outside.py",
        "app\\outside.py",
    ],
)
def test_validate_paths_rejects_protected_traversal_and_non_allowlisted_paths(path: str):
    with pytest.raises(RuntimeError):
        cycle.validate_paths([path])


def test_failed_full_suite_retries_same_task_before_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(cycle, "record", lambda *args: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    def fake_checked(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return "base-sha"
        if command[1:3] == ["branch", "--show-current"]:
            return "codex/local-engineering-123-1-task-1"
        return ""

    monkeypatch.setattr(cycle, "checked", fake_checked)
    monkeypatch.setattr(cycle, "changed_paths", lambda: ["app/fix.py"])
    monkeypatch.setattr(cycle, "prepare_commit", lambda task: "candidate-sha")
    published = []
    monkeypatch.setattr(cycle, "publish_subprocess", lambda *args: published.append(args) or "main-sha")
    calls = {"codex": 0, "pytest": 0}

    def fake_run(command, *, env=None, capture=False):
        if any("autonomous_engineering_progress.py" in str(part) for part in command):
            calls["codex"] += 1
            report = Path(command[command.index("--report") + 1])
            report.write_text("TASK_COMPLETE\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-m" in command and "pytest" in command:
            calls["pytest"] += 1
            return subprocess.CompletedProcess(command, 1 if calls["pytest"] == 1 else 0, "test failure" if calls["pytest"] == 1 else "passed", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cycle, "run", fake_run)
    args = type("Args", (), {
        "run_id": "123", "run_attempt": "1", "model": "test-model",
        "prompt": str(prompt), "mode": "scheduled",
    })()

    assert cycle.run_cycle(args) == 0
    assert calls == {"codex": 2, "pytest": 2}
    assert len(published) == 1
    retry_prompt = tmp_path / "codex-retry-prompt-1-2.md"
    assert "Exact failure output" in retry_prompt.read_text(encoding="utf-8")



def test_git_auth_environment_keeps_token_out_of_child_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    env = cycle.git_auth_env("secret-token")

    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
