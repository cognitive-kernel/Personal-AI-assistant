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


def test_failed_local_and_github_checks_retry_same_task_then_auto_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: {})
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
            "draft": False,
            "merged": True,
            "merge_sha": "e" * 40,
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
        "head": {
            "ref": "codex/local-engineering-100-1-task-1",
            "sha": "a" * 40,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
        "user": {"login": "github-actions[bot]"},
        "draft": False,
        "node_id": "PR_node",
    }])

    result = cycle.find_open_autonomous_pr("token", "owner/repository")

    assert result == {
        "number": 77,
        "url": "https://github.com/owner/repository/pull/77",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-100-1-task-1",
        "draft": False,
        "node_id": "PR_node",
    }


def test_find_open_autonomous_pr_ignores_non_controller_authored_branches(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cycle, "api", lambda *args: [{
        "number": 78,
        "html_url": "https://github.com/owner/repository/pull/78",
        "head": {
            "ref": "codex/local-engineering-101-1-task-1",
            "sha": "a" * 40,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
        "user": {"login": "untrusted-contributor"},
    }])

    assert cycle.find_open_autonomous_pr("token", "owner/repository") is None


def test_complete_pending_pr_rejects_non_controller_author(monkeypatch: pytest.MonkeyPatch):
    head_sha = "a" * 40
    base_sha = "b" * 40
    current = {
        "number": 79,
        "state": "open",
        "user": {"login": "untrusted-contributor"},
        "head": {
            "ref": "codex/local-engineering-102-1-task-1",
            "sha": head_sha,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": base_sha},
    }
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: current)

    with pytest.raises(RuntimeError, match="Pending PR identity or base changed"):
        cycle.complete_pending_pr("token", "owner/repository", {
            "number": 79,
            "head_sha": head_sha,
            "base_sha": base_sha,
        })


def test_run_cycle_recovers_open_task_pr_without_waiting_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    records = []
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: {
        "number": 77, "url": "https://github.com/owner/repository/pull/77",
        "head_sha": "a" * 40, "base_sha": "b" * 40, "branch": "codex/local-engineering-77-1-task-1",
    })
    monkeypatch.setattr(cycle, "complete_pending_pr", lambda *args: {
        "number": 77, "url": "https://github.com/owner/repository/pull/77",
        "head_sha": "a" * 40, "merge_sha": "c" * 40, "merged": True,
    })
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: {})
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: records.append((args, kwargs)))

    args = type("Args", (), {
        "run_id": "200", "run_attempt": "1", "model": "test-model",
        "prompt": "unused", "mode": "continuous",
    })()

    assert cycle.run_cycle(args) == 0
    assert len(records) == 2
    assert records[-1][1]["status"] == "completed"
    assert records[-1][0][2] == "complete"


def test_publish_pull_request_merges_only_after_exact_sha_checks(monkeypatch: pytest.MonkeyPatch):
    base_sha = "b" * 40
    head_sha = "c" * 40
    merge_sha = "e" * 40
    calls = []
    created = {
        "number": 12,
        "draft": False,
        "user": {"login": "github-actions[bot]"},
        "head": {"sha": head_sha, "repo": {"full_name": "owner/repository"}},
        "base": {"ref": "main", "sha": base_sha},
    }
    refreshed = {**created, "html_url": "https://github.com/owner/repository/pull/12"}

    monkeypatch.setattr(cycle, "git_auth_env", lambda token: {})
    monkeypatch.setattr(cycle, "checked", lambda command, **kwargs: calls.append(("git", command)) or "")

    def fake_api(token, repo, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path.startswith("pulls?state=open"):
            return []
        if method == "POST" and path == "pulls":
            assert payload["draft"] is False
            return created
        if method == "GET" and path == "pulls/12":
            return {**refreshed, "merged": True} if any(x[0] == "PUT" for x in calls if len(x) > 1) else refreshed
        if method == "GET" and path == "branches/main":
            return {"commit": {"sha": merge_sha if any(x[0] == "PUT" for x in calls if len(x) > 1) else base_sha}}
        if method == "PUT" and path == "pulls/12/merge":
            assert payload == {"sha": head_sha, "merge_method": "squash"}
            return {"merged": True, "sha": merge_sha}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)
    validations = []
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: validations.append(args))

    result = cycle.publish_pull_request(
        "token", "owner/repository", "codex/local-engineering-1-1-task-1",
        1, "123", head_sha, base_sha,
    )

    assert result == {
        "number": 12,
        "url": "https://github.com/owner/repository/pull/12",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "merge_sha": merge_sha,
        "merged": True,
        "draft": False,
    }
    assert len(validations) == 1
    assert any(call[0] == "PUT" and call[1] == "pulls/12/merge" for call in calls if len(call) > 1)

def test_publish_pull_request_rejects_non_controller_authored_pr(monkeypatch: pytest.MonkeyPatch):
    base_sha = "b" * 40
    head_sha = "c" * 40
    pr = {
        "number": 14,
        "draft": False,
        "user": {"login": "untrusted-contributor"},
        "head": {"sha": head_sha, "repo": {"full_name": "owner/repository"}},
        "base": {"ref": "main", "sha": base_sha},
    }
    monkeypatch.setattr(cycle, "git_auth_env", lambda token: {})
    monkeypatch.setattr(cycle, "checked", lambda command, **kwargs: "")
    monkeypatch.setattr(
        cycle,
        "api",
        lambda token, repo, method, path, payload=None:
            [] if method == "GET" and path.startswith("pulls?state=open")
            else pr if method == "POST" and path == "pulls"
            else None,
    )

    with pytest.raises(RuntimeError, match="Refusing publication"):
        cycle.publish_pull_request(
            "token", "owner/repository", "codex/local-engineering-1-1-task-1",
            1, "123", head_sha, base_sha,
        )


def test_publish_pull_request_rejects_head_changed_after_exact_sha_checks(
    monkeypatch: pytest.MonkeyPatch,
):
    base_sha = "b" * 40
    head_sha = "c" * 40
    changed_sha = "d" * 40
    pr = {
        "number": 13,
        "draft": False,
        "user": {"login": "github-actions[bot]"},
        "head": {"sha": head_sha, "repo": {"full_name": "owner/repository"}},
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

def test_durable_checkpoint_restores_modified_and_new_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import subprocess
    from scripts import autonomous_engineering_checkpoint as checkpoint

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / "docs" / ".keep").write_text("docs\\n", encoding="utf-8")
    (root / "scripts" / ".keep").write_text("scripts\\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Checkpoint Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "checkpoint@example.invalid"], cwd=root, check=True)
    source = root / "app" / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/module.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    source.write_text("VALUE = 2\n", encoding="utf-8")
    new_test = root / "tests" / "test_new.py"
    new_test.write_text("def test_new():\n    assert True\n", encoding="utf-8")
    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "ROOT", root)
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)

    checkpoint.write_snapshot(base_sha, "101", 1)
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=root, check=True, capture_output=True)

    assert checkpoint.restore(base_sha) is True
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert new_test.read_text(encoding="utf-8") == "def test_new():\n    assert True\n"


def test_durable_checkpoint_refuses_a_different_main_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import subprocess
    from scripts import autonomous_engineering_checkpoint as checkpoint

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / "docs" / ".keep").write_text("docs\\n", encoding="utf-8")
    (root / "scripts" / ".keep").write_text("scripts\\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Checkpoint Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "checkpoint@example.invalid"], cwd=root, check=True)
    source = root / "app" / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/module.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "ROOT", root)
    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path / "ProgramData")
    checkpoint.write_snapshot(base_sha, "102", 1)

    with pytest.raises(RuntimeError, match="base SHA differs"):
        checkpoint.restore("f" * 40)

def test_complete_pending_draft_pr_revalidates_checks_then_merges(monkeypatch: pytest.MonkeyPatch):
    base_sha = "b" * 40
    head_sha = "c" * 40
    merge_sha = "e" * 40
    current = {
        "number": 77,
        "state": "open",
        "node_id": "PR_node",
        "draft": True,
        "user": {"login": "github-actions[bot]"},
        "head": {
            "ref": "codex/local-engineering-77-1-task-1",
            "sha": head_sha,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": base_sha},
    }
    ready = {**current, "draft": False}
    confirmed = {**ready, "merged": True}
    calls = []
    responses = [current, ready, {"commit": {"sha": base_sha}}, {"merged": True, "sha": merge_sha}, confirmed, {"commit": {"sha": merge_sha}}]

    def fake_api(token, repo, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path in {"pulls/77", "branches/main"}:
            return responses.pop(0)
        if method == "PUT" and path == "pulls/77/merge":
            assert payload == {"sha": head_sha, "merge_method": "squash"}
            return responses.pop(0)
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)
    monkeypatch.setattr(cycle, "graphql", lambda token, query, variables: {
        "convertPullRequestToReadyForReview": {"pullRequest": {"isDraft": False}}
    })
    validations = []
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: validations.append(args))
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda: None)

    result = cycle.complete_pending_pr("token", "owner/repository", {
        "number": 77,
        "url": "https://github.com/owner/repository/pull/77",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": "codex/local-engineering-77-1-task-1",
        "draft": True,
        "node_id": "PR_node",
    })

    assert result["merged"] is True
    assert result["merge_sha"] == merge_sha
    assert len(validations) == 1
    assert any(call[0] == "PUT" and call[1] == "pulls/77/merge" for call in calls)
