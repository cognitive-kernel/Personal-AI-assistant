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


def test_failed_local_and_github_checks_retry_same_task_then_wait_for_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: None)
    head = ["base-sha"]

    def fake_checked(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return head[0]
        if command[1:3] == ["branch", "--show-current"]:
            return "codex/local-engineering-123-1-task-1"
        return ""

    monkeypatch.setattr(cycle, "checked", fake_checked)
    monkeypatch.setattr(cycle, "changed_paths", lambda: ["app/fix.py"])

    def fake_prepare_commit(task):
        head[0] = "candidate-sha"
        return head[0]

    monkeypatch.setattr(cycle, "prepare_commit", fake_prepare_commit)
    published = []

    def fake_publish(*args):
        published.append(args)
        if len(published) == 1:
            raise RuntimeError("Per-task draft PR publication failed: Required workflow tests.yml failed for candidate SHA.")
        return {
            "number": 42,
            "url": "https://github.com/owner/repository/pull/42",
            "head_sha": "candidate-sha",
            "base_sha": "base-sha",
            "draft": True,
        }

    monkeypatch.setattr(cycle, "publish_pr_subprocess", fake_publish)
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
        "prompt": str(prompt), "mode": "continuous",
    })()

    assert cycle.run_cycle(args) == 0
    assert calls == {"codex": 3, "pytest": 3}
    assert len(published) == 2
    assert published[1][-2:] == ("candidate-sha", "base-sha")
    local_retry_prompt = tmp_path / "codex-retry-prompt-1-2.md"
    ci_retry_prompt = tmp_path / "codex-retry-prompt-1-3.md"
    assert "Exact failure output" in local_retry_prompt.read_text(encoding="utf-8")
    assert "GitHub-hosted verification failed" in ci_retry_prompt.read_text(encoding="utf-8")





def test_find_open_autonomous_pr_detects_pending_generated_review(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cycle, "api", lambda *args: [{
        "number": 77,
        "html_url": "https://github.com/owner/repository/pull/77",
        "head": {"ref": "codex/local-engineering-100-1-task-1", "sha": "a" * 40},
        "base": {"ref": "main"},
    }])

    result = cycle.find_open_autonomous_pr("token", "owner/repository")

    assert result == {
        "number": 77,
        "url": "https://github.com/owner/repository/pull/77",
        "head_sha": "a" * 40,
        "branch": "codex/local-engineering-100-1-task-1",
    }


def test_run_cycle_waits_for_open_task_pr_without_starting_duplicate_work(
    monkeypatch: pytest.MonkeyPatch,
):
    records = []
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: {
        "number": 77, "url": "https://github.com/owner/repository/pull/77",
    })
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: records.append((args, kwargs)))

    args = type("Args", (), {
        "run_id": "200", "run_attempt": "1", "model": "test-model",
        "prompt": "unused", "mode": "continuous",
    })()

    assert cycle.run_cycle(args) == 0
    assert len(records) == 1
    assert records[0][1]["status"] == "awaiting_review"
    assert records[0][0][2] == "review"


def test_publish_pull_request_creates_draft_and_never_calls_merge_api(
    monkeypatch: pytest.MonkeyPatch,
):
    base_sha = "b" * 40
    head_sha = "c" * 40
    calls = []
    created = {
        "number": 12,
        "draft": True,
        "head": {"sha": head_sha},
        "base": {"ref": "main", "sha": base_sha},
    }
    refreshed = {
        **created,
        "html_url": "https://github.com/owner/repository/pull/12",
    }

    monkeypatch.setattr(cycle, "git_auth_env", lambda token: {})
    monkeypatch.setattr(cycle, "checked", lambda command, **kwargs: calls.append(("git", command)) or "")

    def fake_api(token, repo, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path.startswith("pulls?state=open"):
            return []
        if method == "POST" and path == "pulls":
            assert payload["draft"] is True
            return created
        if method == "GET" and path == "pulls/12":
            return refreshed
        if method == "GET" and path == "branches/main":
            return {"commit": {"sha": base_sha}}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)
    validations = []
    monkeypatch.setattr(
        cycle, "dispatch_and_wait_all",
        lambda *args: validations.append(args),
    )

    result = cycle.publish_pull_request(
        "token", "owner/repository", "codex/local-engineering-1-1-task-1",
        1, "123", head_sha, base_sha,
    )

    assert result == {
        "number": 12,
        "url": "https://github.com/owner/repository/pull/12",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "draft": True,
    }
    assert len(validations) == 1
    assert not any(call[0] == "PUT" for call in calls if len(call) > 1)


def test_publish_pull_request_rejects_head_changed_after_exact_sha_checks(
    monkeypatch: pytest.MonkeyPatch,
):
    base_sha = "b" * 40
    head_sha = "c" * 40
    changed_sha = "d" * 40
    pr = {
        "number": 13,
        "draft": True,
        "head": {"sha": head_sha},
        "base": {"ref": "main", "sha": base_sha},
        "html_url": "https://github.com/owner/repository/pull/13",
    }
    refreshed = {
        **pr,
        "head": {"sha": changed_sha},
    }

    monkeypatch.setattr(cycle, "git_auth_env", lambda token: {})
    monkeypatch.setattr(cycle, "checked", lambda command, **kwargs: "")
    monkeypatch.setattr(
        cycle, "api",
        lambda token, repo, method, path, payload=None:
            [] if method == "GET" and path.startswith("pulls?state=open")
            else pr if method == "POST" and path == "pulls"
            else refreshed if method == "GET" and path == "pulls/13"
            else {"commit": {"sha": base_sha}},
    )
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: None)

    with pytest.raises(RuntimeError, match="head or main base changed"):
        cycle.publish_pull_request(
            "token", "owner/repository", "codex/local-engineering-1-1-task-1",
            1, "123", head_sha, base_sha,
        )


def test_git_auth_environment_keeps_token_out_of_child_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    env = cycle.git_auth_env("secret-token")

    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
