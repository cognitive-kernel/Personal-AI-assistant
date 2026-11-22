from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import autonomous_engineering_cycle as cycle


@pytest.fixture(autouse=True)
def allow_direct_publish_state_machine_tests(monkeypatch: pytest.MonkeyPatch):
    from scripts import autonomous_engineering_pull_requests as pull_requests

    monkeypatch.setattr(pull_requests, "require_parent_repository_execution_lock", lambda run_id: None)


def test_timed_stage_reports_started_and_elapsed_duration(monkeypatch, capsys):
    timestamps = iter((10.0, 12.5))
    monkeypatch.setattr(cycle.time, "monotonic", lambda: next(timestamps))

    with cycle.timed_stage("local tests"):
        pass

    output = capsys.readouterr().out
    assert "[autonomous-timing] stage=local_tests status=started" in output
    assert (
        "[autonomous-timing] stage=local_tests status=finished elapsed_seconds=2.5"
        in output
    )


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


def test_failed_local_and_github_checks_stop_at_retry_budget_and_preserve_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_state_tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
    monkeypatch.setattr(cycle, "start_checkpoint_watcher", lambda *args: None)
    stopped = []
    monkeypatch.setattr(cycle, "stop_checkpoint_watcher", lambda *args: stopped.append(args))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(
        cycle, "api",
        lambda token, repo, method, path, payload=None: [] if path == "pulls?state=open&per_page=100" else {},
    )
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

    def fake_publish(*args, **kwargs):
        published.append(args)
        raise RuntimeError("Per-task draft PR publication failed: Required workflow tests.yml failed for candidate SHA.")

    monkeypatch.setattr(cycle, "publish_pr_subprocess", fake_publish)
    cleared = []
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: cleared.append("checkpoint"))
    monkeypatch.setattr(cycle, "clear_pending_state", lambda: cleared.append("pending-state"))
    calls = {"codex": 0, "pytest": 0}

    def fake_run(command, *, env=None, capture=False, timeout_seconds=None):
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

    with pytest.raises(RuntimeError, match=r"attempt limit reached \(2\)"):
        cycle.run_cycle(args)

    assert calls == {"codex": 2, "pytest": 2}
    assert len(published) == 1
    assert published[0][-2:] == ("candidate-sha", "base-sha")
    local_retry_prompt = tmp_path / "codex-retry-prompt-1-2.md"
    assert "Exact failure output" in local_retry_prompt.read_text(encoding="utf-8")
    assert not (tmp_path / "codex-retry-prompt-1-3.md").exists()
    assert len(stopped) == 2
    assert cleared == []
    assert cycle.load_pending_pr() is None


def test_cycle_run_applies_default_command_deadline(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_run_command(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cycle, "_run_command", fake_run_command)

    result = cycle.run(["python", "--version"])

    assert result.returncode == 0
    assert captured["timeout_seconds"] == cycle.DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert captured["cwd"] == cycle.ROOT


def test_cycle_run_converts_timeout_to_a_diagnostic_error(monkeypatch: pytest.MonkeyPatch):
    def fake_run_command(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout_seconds"])

    monkeypatch.setattr(cycle, "_run_command", fake_run_command)

    with pytest.raises(RuntimeError, match="exceeded its 17-second deadline"):
        cycle.run(["python", "--version"], timeout_seconds=17)


def test_run_cycle_retries_same_task_when_codex_report_omits_completion_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
    monkeypatch.setattr(cycle, "start_checkpoint_watcher", lambda *args: None)
    stopped = []
    monkeypatch.setattr(cycle, "stop_checkpoint_watcher", lambda *args: stopped.append(args))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: [])
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: None)
    monkeypatch.setattr(cycle, "restore_checkpoint", lambda *args: False)
    monkeypatch.setattr(
        cycle, "checked",
        lambda command, **kwargs: (
            "base-sha" if command[:3] == ["git", "rev-parse", "HEAD"]
            else "codex/local-engineering-123-1-task-1" if command[1:3] == ["branch", "--show-current"]
            else ""
        ),
    )
    monkeypatch.setattr(
        cycle, "clear_checkpoint",
        lambda *args: (_ for _ in ()).throw(AssertionError("invalid report must not clear the task checkpoint")),
    )
    calls = {"codex": 0}

    def fake_run(command, *, env=None, capture=False, timeout_seconds=None):
        assert any("autonomous_engineering_progress.py" in str(part) for part in command)
        calls["codex"] += 1
        if calls["codex"] == 1:
            report = Path(command[command.index("--report") + 1])
            report.write_text("The implementation is described, but the terminal marker is missing.\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "simulated failure in the repair attempt")

    monkeypatch.setattr(cycle, "run", fake_run)
    args = type("Args", (), {
        "run_id": "123", "run_attempt": "1", "model": "test-model",
        "prompt": str(prompt), "mode": "continuous",
    })()

    with pytest.raises(RuntimeError, match=r"attempt limit reached \(2\)"):
        cycle.run_cycle(args)

    retry_prompt = tmp_path / "codex-retry-prompt-1-2.md"
    retry_text = retry_prompt.read_text(encoding="utf-8")
    assert calls["codex"] == 2
    assert "exactly one valid completion marker" in retry_text
    assert "do not reset or discard the existing worktree/checkpoint" in retry_text
    assert "do not repeat implementation or rerun broad tests" in retry_text
    assert "Fix the root cause, add or update regression tests" not in retry_text
    assert cycle.CODEX_RETRY_POLICY.max_elapsed_seconds == 45 * 60
    assert cycle.TASK_RETRY_POLICY.max_elapsed_seconds == 90 * 60
    assert "on its own final line" in retry_text
    assert "TASK_COMPLETE, QUEUE_EMPTY, TASK_BLOCKED" in retry_text
    assert "Continue the SAME task" in retry_text
    assert len(stopped) == 2
    assert prompt.read_text(encoding="utf-8") == "Complete the selected task."
    assert cycle.load_pending_pr() is None


def test_run_cycle_bounds_failed_codex_attempts_and_preserves_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
    monkeypatch.setattr(cycle, "start_checkpoint_watcher", lambda *args: None)
    stopped = []
    monkeypatch.setattr(cycle, "stop_checkpoint_watcher", lambda *args: stopped.append(args))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: [])
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: None)
    monkeypatch.setattr(cycle, "restore_checkpoint", lambda *args: False)
    monkeypatch.setattr(
        cycle, "checked",
        lambda command, **kwargs: (
            "base-sha" if command[:3] == ["git", "rev-parse", "HEAD"]
            else "codex/local-engineering-123-1-task-1" if command[1:3] == ["branch", "--show-current"]
            else ""
        ),
    )
    monkeypatch.setattr(
        cycle, "clear_checkpoint",
        lambda *args: (_ for _ in ()).throw(AssertionError("failed task checkpoint must not be cleared")),
    )
    calls = {"codex": 0}

    def fake_run(command, *, env=None, capture=False, timeout_seconds=None):
        assert any("autonomous_engineering_progress.py" in str(part) for part in command)
        assert timeout_seconds == cycle.CODEX_TASK_TIMEOUT_SECONDS + 30
        calls["codex"] += 1
        return subprocess.CompletedProcess(command, 1, "", "simulated Codex failure")

    monkeypatch.setattr(cycle, "run", fake_run)
    args = type("Args", (), {
        "run_id": "123", "run_attempt": "1", "model": "test-model",
        "prompt": str(prompt), "mode": "continuous",
    })()

    with pytest.raises(RuntimeError, match=r"attempt limit reached \(2\)"):
        cycle.run_cycle(args)

    assert calls["codex"] == 2
    assert len(stopped) == 2
    assert prompt.read_text(encoding="utf-8") == "Complete the selected task."


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
        }, "102", 1)


def test_run_cycle_recovers_open_task_pr_and_waits_for_owner_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    records = []
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    live_pr = {
        "number": 77,
        "html_url": "https://github.com/owner/repository/pull/77",
        "state": "open",
        "merged": False,
        "user": {"login": "github-actions[bot]"},
        "head": {
            "ref": "codex/local-engineering-77-1-task-1",
            "sha": "a" * 40,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
    }
    monkeypatch.setattr(cycle, "complete_pending_pr", lambda *args: {
        "number": 77, "url": "https://github.com/owner/repository/pull/77",
        "head_sha": "a" * 40, "base_sha": "b" * 40,
        "validated": True, "merged": False, "awaiting_review": True,
    })
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: (_ for _ in ()).throw(AssertionError("checkpoint cleared before owner merge")))
    monkeypatch.setattr(cycle, "clear_pending_state", lambda: (_ for _ in ()).throw(AssertionError("pending state cleared before owner merge")))
    monkeypatch.setattr(
        cycle, "api",
        lambda token, repo, method, path, payload=None: (
            [live_pr] if path == "pulls?state=open&per_page=100"
            else live_pr if path == "pulls/77"
            else {}
        ),
    )
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: records.append((args, kwargs)))

    args = type("Args", (), {
        "run_id": "200", "run_attempt": "1", "model": "test-model",
        "prompt": "unused", "mode": "continuous",
    })()

    assert cycle.run_cycle(args) == 0
    assert len(records) == 2
    assert records[-1][0][2] == "publication"
    assert "بازبینی مالک" in records[-1][0][4]


def test_publish_pull_request_leaves_exact_sha_validated_pr_open_for_owner_merge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path):
    base_sha = "b" * 40
    head_sha = "c" * 40
    calls = []
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
    created = {
        "number": 12,
        "state": "open",
        "merged": False,
        "draft": False,
        "html_url": "https://github.com/owner/repository/pull/12",
        "user": {"login": "github-actions[bot]"},
        "merged_by": {"login": "repository-owner", "type": "User"},
        "head": {"sha": head_sha, "repo": {"full_name": "owner/repository"}},
        "base": {"ref": "main", "sha": base_sha},
    }
    refreshed = {**created}

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
            return refreshed
        if method == "GET" and path == "branches/main":
            return {"commit": {"sha": base_sha}}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)
    validations = []

    def dispatch_after_owner_record(*args):
        validations.append(args)
        assert cycle.load_pending_pr()["number"] == 12

    monkeypatch.setattr(cycle, "dispatch_and_wait_all", dispatch_after_owner_record)

    result = cycle.publish_pull_request(
        "token", "owner/repository", "codex/local-engineering-1-1-task-1",
        1, "123", head_sha, base_sha,
    )

    assert result == {
        "number": 12,
        "url": "https://github.com/owner/repository/pull/12",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": "codex/local-engineering-1-1-task-1",
        "run_id": "123",
        "task": 1,
        "validated": True,
        "merged": False,
        "awaiting_review": True,
        "draft": False,
    }
    assert len(validations) == 1
    assert not any(call[0] == "PUT" and str(call[1]).endswith("/merge") for call in calls if len(call) > 1)
    created_payload = next(call[2] for call in calls if call[0] == "POST" and call[1] == "pulls")
    assert "\n" in created_payload["body"]
    assert "\\n" not in created_payload["body"]
    assert cycle.load_pending_pr()["number"] == 12

def test_publish_pull_request_rejects_non_controller_authored_pr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    base_sha = "b" * 40
    head_sha = "c" * 40
    monkeypatch.setattr(cycle, "_autonomous_state_dir", lambda: tmp_path / "state")
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    base_sha = "b" * 40
    head_sha = "c" * 40
    changed_sha = "d" * 40
    monkeypatch.setattr(cycle, "_autonomous_state_dir", lambda: tmp_path / "state")
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

    with pytest.raises(RuntimeError, match="PR head changed during exact-SHA validation"):
        cycle.publish_pull_request(
            "token", "owner/repository", "codex/local-engineering-1-1-task-1",
            1, "123", head_sha, base_sha,
        )

    # The durable owner record must be written under the exact same root whose
    # lock protected publication, even when later exact-SHA validation fails.
    import json
    saved = json.loads((tmp_path / "state" / "pending-autonomous-pr.json").read_text(encoding="utf-8"))
    assert saved["number"] == 13
    assert saved["head_sha"] == head_sha


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
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    source.write_text("VALUE = 2\n", encoding="utf-8")
    new_test = root / "tests" / "test_new.py"
    new_test.write_text("def test_new():\n    assert True\n", encoding="utf-8")
    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "ROOT", root)
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)

    shared_index = root / ".git" / "index"
    index_before = shared_index.read_bytes()
    checkpoint.write_snapshot(base_sha, "101", 1)
    assert shared_index.read_bytes() == index_before, (
        "Checkpoint snapshotting must not mutate the shared Git index"
    )
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=root, check=True, capture_output=True)

    assert checkpoint.restore(base_sha) is True
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert new_test.read_text(encoding="utf-8") == "def test_new():\n    assert True\n"


def test_durable_checkpoint_restores_onto_a_new_main_when_patch_context_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
    source.write_text("VALUE = 1\\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    source.write_text("VALUE = 2\\n", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "ROOT", root)
    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)
    checkpoint.write_snapshot(base_sha, "102", 1)

    (root / "docs" / "main-update.md").write_text("unrelated main update\\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/main-update.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "unrelated main update"], cwd=root, check=True, capture_output=True)
    current_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "reset", "--hard", current_sha], cwd=root, check=True, capture_output=True)

    assert current_sha != base_sha
    assert checkpoint.restore(current_sha) is True
    assert source.read_text(encoding="utf-8") == "VALUE = 2\\n"
    assert (root / "docs" / "main-update.md").read_text(encoding="utf-8") == "unrelated main update\\n"


def test_durable_checkpoint_refreshes_watcher_metadata_when_patch_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import json
    import subprocess
    from scripts import autonomous_engineering_checkpoint as checkpoint

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    source = root / "app" / "module.py"
    source.write_text("VALUE = 1\\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Checkpoint Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "checkpoint@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    source.write_text("VALUE = 2\\n", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "ROOT", root)
    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)

    checkpoint.write_snapshot(base_sha, "103", 1)
    original_patch = checkpoint.paths("103", 1)["patch"].read_bytes()
    checkpoint.write_snapshot(base_sha, "103", 1, watcher_pid=4321)

    metadata = json.loads(checkpoint.paths("103", 1)["metadata"].read_text(encoding="utf-8"))
    assert metadata["watcher_pid"] == 4321
    assert checkpoint.paths("103", 1)["patch"].read_bytes() == original_patch


def test_checkpoint_watcher_persists_initial_snapshot_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from scripts import autonomous_engineering_checkpoint as checkpoint

    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)

    def fail_snapshot(*args, **kwargs):
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(checkpoint, "_write_snapshot_unlocked", fail_snapshot)

    with pytest.raises(RuntimeError, match="simulated snapshot failure"):
        checkpoint.watch("a" * 40, "104", 1, 5)

    assert "simulated snapshot failure" in (state / "worktree-checkpoint-104-1.error").read_text(encoding="utf-8")
    assert not (state / "worktree-checkpoint-104-1.ready").exists()


def test_checkpoint_watcher_real_process_publishes_run_scoped_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    import json
    import os
    import sys
    import time

    from scripts import autonomous_engineering_checkpoint as checkpoint

    root = Path(__file__).resolve().parents[1]
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    program_data = shared_state_tmp_path / "ProgramData"
    state = program_data / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    env = os.environ.copy()
    env.pop("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", None)
    env["PROGRAMDATA"] = str(program_data)
    run_id = f"integration-{os.getpid()}"
    task = 91
    # Match the production launch path: on Windows, bypass the venv launcher
    # so the process PID recorded by the watcher is the Popen PID.
    watcher_executable = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [
            watcher_executable,
            str(root / "scripts" / "autonomous_engineering_checkpoint.py"),
            "watch",
            "--base-sha", base_sha,
            "--run-id", run_id,
            "--task", str(task),
            "--interval", "5",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ready_path = state / f"worktree-checkpoint-{run_id}-{task}.ready"
    error_path = state / f"worktree-checkpoint-{run_id}-{task}.error"
    metadata_path = checkpoint.paths(run_id, task)["metadata"]
    try:
        deadline = time.monotonic() + 20
        metadata = None
        while time.monotonic() < deadline and process.poll() is None:
            if error_path.is_file():
                break
            if metadata_path.is_file():
                try:
                    candidate = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    candidate = None
                if isinstance(candidate, dict) and candidate.get("watcher_ready") is True:
                    metadata = candidate
                    break
            time.sleep(0.1)
        assert not error_path.exists(), (
            error_path.read_text(encoding="utf-8") if error_path.exists() else ""
        )
        assert metadata is not None, (
            process.stdout.read() if process.poll() is not None else "watcher readiness timed out"
        )
        assert metadata["schema_version"] == 4
        assert metadata["base_sha"] == base_sha
        assert metadata["run_id"] == run_id
        assert metadata["task"] == task
        assert metadata["watcher_pid"] == process.pid
        assert isinstance(metadata["watcher_started_at"], (int, float))
        assert metadata["watcher_started_at"] > 0
        assert not ready_path.exists(), "Readiness must not depend on a secondary marker file"
    finally:
        checkpoint.paths(run_id, task)["stop"].write_text("stop\\n", encoding="utf-8")
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_checkpoint_readiness_is_committed_without_a_secondary_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    import json
    from scripts import autonomous_engineering_checkpoint as checkpoint
    from scripts.autonomous_engineering_checkpoint_contract import build_checkpoint_metadata

    state_root = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state_root)

    def prepare_snapshot(base_sha, run_id, task, watcher_pid, watcher_started_at, *, state=None):
        paths = state if state is not None else checkpoint.paths(run_id, task)
        state_root.mkdir(parents=True, exist_ok=True)
        metadata = build_checkpoint_metadata(
            base_sha=base_sha,
            run_id=run_id,
            task=task,
            watcher_pid=watcher_pid,
            watcher_started_at=watcher_started_at,
            updated_at=1.0,
            patch_bytes=0,
        )
        checkpoint._atomic_write(
            paths["metadata"], json.dumps(metadata, sort_keys=True) + "\n"
        )
        paths["stop"].write_text("stop\n", encoding="utf-8")

    monkeypatch.setattr(checkpoint, "_write_snapshot_unlocked", prepare_snapshot)
    assert checkpoint.watch("a" * 40, "106", 1, 5) == 0

    metadata = json.loads(
        (state_root / "worktree-checkpoint-106-1.json").read_text(encoding="utf-8")
    )
    assert metadata["watcher_ready"] is True
    assert metadata["watcher_pid"] == __import__("os").getpid()
    assert not (state_root / "worktree-checkpoint-106-1.ready").exists()
    assert not (state_root / "worktree-checkpoint-106-1.error").exists()

def test_checkpoint_watcher_cannot_lose_a_fast_readiness_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path):
    import json
    import os
    from types import SimpleNamespace
    from scripts import autonomous_engineering_checkpoint as checkpoint

    state = shared_state_tmp_path / "ProgramData" / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)
    state.mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(shared_state_tmp_path / "ProgramData"))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.setattr(
        cycle, "os",
        SimpleNamespace(name="nt", getenv=os.getenv, environ=os.environ, path=os.path, pathsep=os.pathsep),
    )
    ticks = iter((0.0, 0.0, 31.0))
    monkeypatch.setattr(cycle.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cycle.time, "sleep", lambda _: None)

    class FakeProcess:
        pid = 4321

        def __init__(self, *args, **kwargs):
            checkpoint.paths("105", 1)["metadata"].write_text(
                json.dumps({
                    "schema_version": 4,
                    "watcher_ready": True,
                    "run_id": "105",
                    "task": 1,
                    "base_sha": "a" * 40,
                    "watcher_pid": 4321,
                    "watcher_started_at": 1234.5,
                }),
                encoding="utf-8",
            )

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cycle.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cycle, "process_start_time", lambda pid: 1234.5)

    assert cycle.start_checkpoint_watcher("a" * 40, "105", 1) is not None


def test_complete_pending_draft_pr_revalidates_checks_and_leaves_pr_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path):
    base_sha = "b" * 40
    head_sha = "c" * 40
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "autonomous-state"))
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
        "html_url": "https://github.com/owner/repository/pull/77",
    }
    ready = {**current, "draft": False}
    calls = []
    responses = [current, ready, {"commit": {"sha": base_sha}}]

    def fake_api(token, repo, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path in {"pulls/77", "branches/main"}:
            return responses.pop(0)
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)
    monkeypatch.setattr(cycle, "graphql", lambda token, query, variables: {
        "convertPullRequestToReadyForReview": {"pullRequest": {"isDraft": False}}
    })
    validations = []
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: validations.append(args))
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: (_ for _ in ()).throw(AssertionError("checkpoint cleared before owner merge")))

    result = cycle.complete_pending_pr("token", "owner/repository", {
        "number": 77,
        "url": "https://github.com/owner/repository/pull/77",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": "codex/local-engineering-77-1-task-1",
        "draft": True,
        "node_id": "PR_node",
    }, "77", 1)

    assert result["validated"] is True
    assert result["merged"] is False
    assert result["awaiting_review"] is True
    assert len(validations) == 1
    assert not any(call[0] == "PUT" and str(call[1]).endswith("/merge") for call in calls)
    assert cycle.load_pending_pr()["number"] == 77



def test_checkpoint_readiness_contract_rejects_wrong_owner_or_base():
    from scripts.autonomous_engineering_checkpoint_contract import (
        build_checkpoint_metadata,
        metadata_confirms_watcher_ready,
    )

    metadata = build_checkpoint_metadata(
        base_sha="a" * 40,
        run_id="run-1",
        task=2,
        watcher_pid=4321,
        watcher_started_at=1234.5,
        updated_at=1.0,
        patch_bytes=0,
    )
    assert metadata_confirms_watcher_ready(
        metadata, base_sha="a" * 40, run_id="run-1", task=2, watcher_pid=4321
    )
    assert not metadata_confirms_watcher_ready(
        metadata, base_sha="b" * 40, run_id="run-1", task=2, watcher_pid=4321
    )
    assert not metadata_confirms_watcher_ready(
        metadata, base_sha="a" * 40, run_id="run-2", task=2, watcher_pid=4321
    )
    assert not metadata_confirms_watcher_ready(
        metadata, base_sha="a" * 40, run_id="run-1", task=2, watcher_pid=4322
    )
    assert not metadata_confirms_watcher_ready(
        metadata, base_sha="a" * 40, run_id="run-1", task=2,
        watcher_pid=4321, watcher_started_at=9999.0,
    )

def test_checkpoint_paths_are_isolated_for_each_run_and_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from scripts import autonomous_engineering_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    first = checkpoint.paths("run-100", 1)
    second = checkpoint.paths("run-200", 1)
    third = checkpoint.paths("run-100", 2)

    assert first["patch"] != second["patch"]
    assert first["metadata"] != second["metadata"]
    assert first["stop"] != second["stop"]
    assert first["patch"] != third["patch"]
    assert first["plan"] != third["plan"]


def test_repository_execution_lock_rejects_a_second_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path):
    import os
    import sys
    from scripts import autonomous_engineering_cycle as cycle

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path))
    code = (
        "from scripts.autonomous_engineering_cycle import repository_execution_lock; "
        "lock=repository_execution_lock('second-run','1'); lock.__enter__(); lock.__exit__(None,None,None)"
    )
    with cycle.repository_execution_lock("first-run", "1"):
        child = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert child.returncode != 0
        assert "Another autonomous execution owns this repository worktree" in child.stderr



def test_checkpoint_watcher_uses_base_interpreter_with_venv_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    import json
    import os
    import sys
    from types import SimpleNamespace
    from scripts import autonomous_engineering_checkpoint as checkpoint
    from scripts import autonomous_engineering_cycle as cycle

    state = shared_state_tmp_path / "ProgramData" / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)
    monkeypatch.setenv("PROGRAMDATA", str(shared_state_tmp_path / "ProgramData"))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.setattr(
        cycle, "os",
        SimpleNamespace(name="nt", getenv=os.getenv, environ=os.environ, path=os.path, pathsep=os.pathsep),
    )
    monkeypatch.setattr(cycle.sys, "_base_executable", str(tmp_path / "python.exe"), raising=False)
    monkeypatch.setattr(cycle.sys, "executable", str(tmp_path / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(cycle.sysconfig, "get_paths", lambda: {
        "purelib": str(tmp_path / ".venv" / "Lib" / "site-packages")
    })
    monkeypatch.setenv("PYTHONPATH", "existing-python-path")
    captured = {}

    class FakeProcess:
        pid = 4321

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            checkpoint.paths("107", 1)["metadata"].write_text(
                json.dumps({
                    "schema_version": 4, "watcher_ready": True,
                    "run_id": "107", "task": 1, "base_sha": "a" * 40,
                    "watcher_pid": self.pid,
                    "watcher_started_at": 1234.5,
                }),
                encoding="utf-8",
            )

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cycle.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cycle, "process_start_time", lambda pid: 1234.5)
    monkeypatch.setattr(cycle.time, "monotonic", lambda: 0.0)

    assert cycle.start_checkpoint_watcher("a" * 40, "107", 1) is not None
    assert captured["command"][0] == str(tmp_path / "python.exe")
    assert captured["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(tmp_path / ".venv" / "Lib" / "site-packages"),
        "existing-python-path",
    ]



def test_windows_codex_command_uses_call_without_nested_cmd_quoting(monkeypatch, tmp_path: Path):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    executable = str(tmp_path / "profile" / "AppData" / "Roaming" / "npm" / "codex.CMD")
    command = progress.codex_command(executable, ["exec", "--json", "-"])

    assert command[0] == progress.sys.executable
    assert command[1].endswith("autonomous_engineering_codex_runner.py")
    assert command[command.index("--timeout-seconds") + 1] == "1800"
    native_start = command.index("--") + 1
    assert command[native_start:] == [
        r"C:\Windows\System32\cmd.exe", "/d", "/c", "call",
        executable, "exec", "--json", "-",
    ]


def test_codex_arguments_pin_ollama_and_selected_windows_sandbox(monkeypatch):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "elevated")
    arguments = progress.codex_arguments("gpt-oss:20b", "codex-report.md")

    assert arguments[:5] == [
        "exec", "-c", 'model_provider="ollama-local"',
        "-c", 'windows.sandbox="elevated"',
    ]
    overrides = [
        arguments[i + 1] for i, value in enumerate(arguments[:-1]) if value == "-c"
    ]
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in overrides
    assert any(value.startswith("model_catalog_json=") for value in overrides)
    assert 'model_providers.ollama-local.name="Ollama local"' in overrides
    assert 'model_providers.ollama-local.base_url="http://127.0.0.1:11434/v1/"' in overrides
    assert 'model_providers.ollama-local.wire_api="responses"' in overrides
    assert "model_providers.ollama-local.requires_openai_auth=false" in overrides
    assert "--oss" not in arguments
    assert arguments[arguments.index("--sandbox"):arguments.index("--sandbox") + 2] == [
        "--sandbox", "workspace-write",
    ]
    # Keep the custom provider separate from Codex's reserved built-in namespace.
    assert not any(value.startswith("model_providers.ollama.") for value in overrides)
    assert arguments[arguments.index("--model") + 1] == "gpt-oss:20b"


def test_codex_arguments_reject_unverified_windows_sandbox_backend(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "danger-full-access")
    with pytest.raises(ValueError, match=r"must be 'mxc', 'elevated', or 'unelevated'"):
        progress.codex_arguments("gpt-oss:20b", "codex-report.md")


def test_codex_arguments_allow_restricted_unelevated_backend(monkeypatch):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "unelevated")
    arguments = progress.codex_arguments("gpt-oss:20b", "codex-report.md")

    assert 'windows.sandbox="unelevated"' in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "workspace-write"


def test_autonomous_workflow_gates_unelevated_sandbox_behind_explicit_opt_in():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-engineering.yml"
    ).read_text(encoding="utf-8")

    assert "AUTONOMOUS_ENGINEERING_ALLOW_UNELEVATED_SANDBOX" in workflow
    assert "$sandboxBackends = @(\"mxc\")" in workflow
    assert 'if ($env:AUTONOMOUS_ENGINEERING_ALLOW_UNELEVATED_SANDBOX -eq "true")' in workflow
    assert "weaker network isolation" in workflow
    assert "disabled by default" in workflow


def test_run_codex_handles_early_child_exit_without_broken_pipe_traceback(monkeypatch, tmp_path, shared_state_tmp_path):
    from types import SimpleNamespace
    from scripts import autonomous_engineering_progress as progress

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do the task", encoding="utf-8")
    monkeypatch.setattr(progress.shutil, "which", lambda _: r"C:\\npm\\codex.CMD")
    monkeypatch.setattr(progress, "codex_command", lambda executable, arguments, **kwargs: ["cmd.exe", "/d", "/c", "call", executable, *arguments])
    monkeypatch.setattr(progress, "persist", lambda record: True)
    class NoOpProcessJob:
        CREATE_SUSPENDED = progress.WindowsProcessJob.CREATE_SUSPENDED

        def __enter__(self):
            return self

        def assign_and_resume(self, process):
            return None

        def terminate_remaining_processes(self, exit_code, timeout_seconds=5.0):
            return False

        def close(self):
            return None

    monkeypatch.setattr(progress, "WindowsProcessJob", NoOpProcessJob)
    monkeypatch.setattr(progress, "PLAN_FILE", tmp_path / ".agent-progress.json")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "progress"))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")

    class BrokenInput:
        def write(self, value):
            raise BrokenPipeError(32, "pipe closed")

        def close(self):
            return None

    class FakeProcess:
        stdin = BrokenInput()
        stdout = iter(())
        returncode = 1

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(progress.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    args = SimpleNamespace(
        model="gpt-oss:20b", prompt=str(prompt), report=str(tmp_path / "report.md"),
        run_id="123", run_attempt="1",
    )

    assert progress.run_codex(args) == 1


def test_codex_smoke_evidence_requires_a_real_successful_command_and_final_turn():
    from scripts import autonomous_engineering_progress as progress

    command_text = (
        "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); "
        "p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); "
        "print('CODEX_WORKSPACE_SANDBOX_OK')"
    )
    events = [
        {"type": "item.started", "item": {
            "id": "command-1", "type": "command_execution",
            "command": command_text, "status": "in_progress",
        }},
        # Codex CLI completion events may omit command; correlate by item.id.
        {"type": "item.completed", "item": {
            "id": "command-1", "type": "command_execution",
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK\\n",
            "exit_code": 0, "status": "completed",
        }},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        }},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]

    evidence = progress.codex_smoke_evidence(
        events, "CODEX_WORKSPACE_SANDBOX_OK", "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        [".codex-sandbox-probe.tmp", "write_text", "read_text", "unlink"],
    )

    assert evidence["passed"] is True
    assert all(evidence["checks"].values())
    assert evidence["command_exit_code"] == 0
    assert evidence["command_started_count"] == 1
    assert evidence["command_completed_count"] == 1
    assert evidence["agent_message_count"] == 1
    assert evidence["turn_usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert "command" not in evidence
    assert "agent_message" not in evidence
    assert all("command" not in item for item in evidence["command_summaries"])




def test_codex_smoke_evidence_exposes_only_sanitized_command_failure_details():
    from scripts import autonomous_engineering_progress as progress

    events = [
        {"type": "item.started", "item": {
            "id": "command-1", "type": "command_execution",
            "command": "python -c private-command", "status": "in_progress",
        }},
        {"type": "item.completed", "item": {
            "id": "command-1", "type": "command_execution",
            "aggregated_output": (
                "The system cannot find the path specified: "
                "C:\\Users\\runner-svc\\AppData\\Local\\Temp\\private.tmp"
            ),
            "exit_code": 1, "status": "failed",
        }},
        {"type": "turn.completed"},
    ]

    evidence = progress.codex_smoke_evidence(
        events, "CODEX_WORKSPACE_SANDBOX_OK", "", ["private-command"],
    )

    failure = evidence["command_summaries"][0]
    assert evidence["passed"] is False
    assert failure["failure_category"] == "path_not_found"
    assert "aggregated_output" in failure["failure_field_names"]
    assert "<windows-path>" in failure["failure_signature"]
    assert "runner-svc" not in failure["failure_signature"]
    assert "private-command" not in failure["failure_signature"]
    assert "private.tmp" not in failure["failure_signature"]

def test_codex_smoke_evidence_accepts_multiple_successful_calls_with_one_verified_probe():
    from scripts import autonomous_engineering_progress as progress

    probe = (
        "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); "
        "p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); "
        "print('CODEX_WORKSPACE_SANDBOX_OK')"
    )
    events = [
        {"type": "item.started", "item": {
            "id": "command-1", "type": "command_execution",
            "command": "python -c print('bounded-precheck')", "status": "in_progress",
        }},
        {"type": "item.completed", "item": {
            "id": "command-1", "type": "command_execution",
            "aggregated_output": "bounded-precheck", "exit_code": 0, "status": "completed",
        }},
        {"type": "item.started", "item": {
            "id": "command-2", "type": "command_execution",
            "command": probe, "status": "in_progress",
        }},
        {"type": "item.completed", "item": {
            "id": "command-2", "type": "command_execution",
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK", "exit_code": 0, "status": "completed",
        }},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        }},
        {"type": "turn.completed"},
    ]

    evidence = progress.codex_smoke_evidence(
        events, "CODEX_WORKSPACE_SANDBOX_OK", "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        [".codex-sandbox-probe.tmp", "write_text", "read_text", "unlink"],
    )

    assert evidence["passed"] is True
    assert evidence["command_started_count"] == 2
    assert evidence["command_completed_count"] == 2
    assert evidence["checks"]["command_events_are_paired"] is True
    assert evidence["checks"]["all_commands_completed_successfully"] is True
    assert evidence["checks"]["exactly_one_verified_workspace_probe"] is True


def test_codex_tool_smoke_does_not_require_a_final_text_message():
    from scripts import autonomous_engineering_progress as progress

    command = (
        "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); "
        "p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); "
        "print('CODEX_WORKSPACE_SANDBOX_OK')"
    )
    events = [
        {"type": "item.started", "item": {
            "id": "command-1", "type": "command_execution",
            "command": command, "status": "in_progress",
        }},
        {"type": "item.completed", "item": {
            "id": "command-1", "type": "command_execution",
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK",
            "exit_code": 0, "status": "completed",
        }},
        {"type": "turn.completed"},
    ]

    evidence = progress.codex_smoke_evidence(
        events, "CODEX_WORKSPACE_SANDBOX_OK", "",
        [".codex-sandbox-probe.tmp", "write_text", "read_text", "unlink"],
    )

    assert evidence["passed"] is True
    assert evidence["agent_message_count"] == 0
    assert evidence["checks"]["expected_agent_message"] is True
    assert evidence["agent_message_matches_expected"] is True



@pytest.mark.parametrize(
    "mutation",
    [
        "no_command",
        "nonzero_exit",
        "missing_output_marker",
        "no_turn_completed",
        "turn_failed",
        "duplicate_command",
        "wrong_command",
        "missing_command_start",
        "mismatched_command_start",
        "wrong_agent_message",
        "item_error",
    ],
)
def test_codex_smoke_evidence_rejects_unproven_or_failed_execution(mutation: str):
    from scripts import autonomous_engineering_progress as progress

    command_text = (
        "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); "
        "p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); "
        "print('CODEX_WORKSPACE_SANDBOX_OK')"
    )
    command = {
        "type": "item.completed",
        "item": {
            "id": "command-1", "type": "command_execution",
            "command": command_text,
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK\\n",
            "exit_code": 0, "status": "completed",
        },
    }
    command_started = {"type": "item.started", "item": {
        "id": "command-1", "type": "command_execution",
        "command": command_text, "status": "in_progress",
    }}
    message = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "OLLAMA_PROVIDER_AND_SANDBOX_OK"},
    }
    events = [command_started, command, message, {"type": "turn.completed"}]

    if mutation == "no_command":
        events.remove(command)
    elif mutation == "nonzero_exit":
        command["item"]["exit_code"] = 1
        command["item"]["status"] = "failed"
    elif mutation == "missing_output_marker":
        command["item"]["aggregated_output"] = "different output"
    elif mutation == "no_turn_completed":
        events.remove(events[-1])
    elif mutation == "turn_failed":
        events.append({"type": "turn.failed", "error": {"message": "failed"}})
    elif mutation == "duplicate_command":
        events.insert(1, dict(command))
    elif mutation == "wrong_command":
        command["item"]["command"] = "python -c print(CODEX_WORKSPACE_SANDBOX_OK)"
    elif mutation == "missing_command_start":
        events.remove(command_started)
    elif mutation == "mismatched_command_start":
        command_started["item"]["command"] = "python -c print(CODEX_WORKSPACE_SANDBOX_OK)"
    elif mutation == "wrong_agent_message":
        message["item"]["text"] = "I think it worked"
    elif mutation == "item_error":
        events.append({
            "type": "item.completed",
            "item": {"type": "error", "message": "tool execution failed"},
        })

    evidence = progress.codex_smoke_evidence(
        events, "CODEX_WORKSPACE_SANDBOX_OK", "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        [".codex-sandbox-probe.tmp", "write_text", "read_text", "unlink"],
    )

    assert evidence["passed"] is False
    assert not all(evidence["checks"].values())

def test_codex_smoke_evidence_report_is_written_atomically(tmp_path: Path):
    import json
    from scripts import autonomous_engineering_progress as progress

    destination = tmp_path / "nested" / "evidence.json"
    evidence = {"passed": True, "checks": {"command_exit_code_zero": True}}

    progress.write_smoke_evidence(str(destination), evidence)

    assert json.loads(destination.read_text(encoding="utf-8")) == evidence
    assert not destination.with_suffix(".json.tmp").exists()

def test_windows_sandbox_diagnostic_cancels_only_older_queued_runs_and_uses_unique_probe_paths():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    cleanup_start = workflow.index("  cancel-stale-diagnostics:")
    sandbox_start = workflow.index("  sandbox-diagnostic:")
    cleanup_job = workflow[cleanup_start:sandbox_start]
    sandbox_job = workflow[sandbox_start:]

    assert "runs-on: ubuntu-latest" in cleanup_job
    assert "actions: write" in cleanup_job
    assert "continue-on-error: true" in cleanup_job
    assert "run.status -eq \"queued\"" in cleanup_job
    assert "actions/runs/$($run.id)/jobs?per_page=100" in cleanup_job
    assert "run.status -eq \"in_progress\"" in cleanup_job
    assert "activeSandbox" in cleanup_job
    assert "sandboxJobs | Where-Object { $_.status -eq \"in_progress\" }" in cleanup_job
    assert "$shouldCancel = -not $activeSandbox" in cleanup_job
    assert "run.run_number -ge" in cleanup_job
    assert "continue" in cleanup_job
    assert "/cancel" in cleanup_job
    assert "/force-cancel" in cleanup_job
    assert "concurrency:" not in cleanup_job
    assert "needs: cancel-stale-diagnostics" in sandbox_job
    sandbox_job_header = sandbox_job.split("steps:", 1)[0]
    assert "if: always()" not in sandbox_job_header
    assert "scripts/verify_ollama_context.ps1" in workflow.replace("\\", "/")
    assert "GITHUB_RUN_ID" in sandbox_job
    assert "GITHUB_RUN_ATTEMPT" in sandbox_job
    assert ".codex-sandbox-probe-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT).tmp" in sandbox_job
    assert "--require-command-fragment \"$probeFileName\"" in sandbox_job
    assert ".codex-sandbox-probe.tmp" not in sandbox_job

def test_complete_merged_pr_requires_owner_merge_and_post_merge_exact_sha_checks(monkeypatch: pytest.MonkeyPatch):
    merge_sha = "e" * 40
    head_sha = "c" * 40
    pending = {
        "number": 88,
        "url": "https://github.com/owner/repository/pull/88",
        "head_sha": head_sha,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-88-1-task-1",
        "run_id": "88",
        "task": 1,
    }
    current = {
        "number": 88,
        "state": "closed",
        "merged": True,
        "merge_commit_sha": merge_sha,
        "html_url": "https://github.com/owner/repository/pull/88",
        "user": {"login": "github-actions[bot]"},
        "merged_by": {"login": "repository-owner", "type": "User"},
        "head": {
            "ref": pending["branch"],
            "sha": head_sha,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": merge_sha},
    }
    calls = []
    monkeypatch.setattr(cycle, "api", lambda token, repo, method, path, payload=None: (
        calls.append((method, path, payload))
        or current if path == "pulls/88"
        else {"commit": {"sha": merge_sha}}
    ))
    validations = []
    cleared = []
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: validations.append(args))
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: cleared.append("checkpoint"))
    monkeypatch.setattr(cycle, "clear_pending_state", lambda: cleared.append("pending-state"))

    result = cycle.complete_merged_pr("token", "owner/repository", pending)

    assert result["merged"] is True
    assert result["post_merge_validated"] is True
    assert result["merge_sha"] == merge_sha
    assert validations == [("token", "owner/repository", ("tests.yml", "browser-e2e.yml", "dependency-audit.yml"), "main", merge_sha)]
    assert cleared == ["checkpoint", "pending-state"]
    assert not any(method == "PUT" and str(path).endswith("/merge") for method, path, _ in calls)


def test_complete_merged_pr_rejects_bot_merge_actor(monkeypatch: pytest.MonkeyPatch):
    merge_sha = "f" * 40
    pending = {
        "number": 89,
        "url": "https://github.com/owner/repository/pull/89",
        "head_sha": "d" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-89-1-task-1",
        "run_id": "89",
        "task": 1,
    }
    current = {
        "number": 89,
        "state": "closed",
        "merged": True,
        "merge_commit_sha": merge_sha,
        "html_url": pending["url"],
        "user": {"login": "github-actions[bot]"},
        "merged_by": {"login": "github-actions[bot]", "type": "Bot"},
        "head": {"ref": pending["branch"], "sha": pending["head_sha"], "repo": {"full_name": "owner/repository"}},
        "base": {"ref": "main", "sha": merge_sha},
    }
    calls = []
    monkeypatch.setattr(cycle, "api", lambda token, repo, method, path, payload=None: (
        calls.append((method, path, payload)) or current
    ))
    cleared = []
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: cleared.append("checkpoint"))
    monkeypatch.setattr(cycle, "clear_pending_state", lambda: cleared.append("pending-state"))
    monkeypatch.setattr(cycle, "dispatch_and_wait_all", lambda *args: pytest.fail("bot merge must not advance the queue"))

    with pytest.raises(RuntimeError, match="exact reviewed candidate"):
        cycle.complete_merged_pr("token", "owner/repository", pending)
    assert cleared == []
    assert not any(method == "PUT" and str(path).endswith("/merge") for method, path, _ in calls)


def test_pending_pr_state_round_trips_atomically_and_rejects_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_state_tmp_path: Path):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "state"))
    pending = {
        "number": 91,
        "url": "https://github.com/owner/repository/pull/91",
        "head_sha": "c" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-91-1-task-1",
        "run_id": "91",
        "task": 1,
    }

    cycle.save_pending_pr(pending, "owner/repository")
    assert cycle.load_pending_pr() == pending
    assert not cycle._pending_pr_path().with_name(cycle._pending_pr_path().name + f".{__import__('os').getpid()}.tmp").exists()

    cycle._pending_pr_path().write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        cycle.load_pending_pr()


def test_codex_arguments_refuse_to_invent_context_without_ollama_preflight(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", raising=False)
    with pytest.raises(ValueError, match="verified Ollama context preflight"):
        progress.codex_arguments("qwen3:14b-codex-ctx32768", "codex-report.md")


def test_clear_pending_state_removes_task_and_pr_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    state_dir = shared_state_tmp_path / "durable-state"
    state_dir.mkdir()
    (state_dir / "pending-manual-task.txt").write_text("task", encoding="utf-8")
    (state_dir / "pending-autonomous-pr.json").write_text("{}", encoding="utf-8")
    (state_dir / "pending-manual-task-owner.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(state_dir))

    cycle.clear_pending_state()

    assert not (state_dir / "pending-manual-task.txt").exists()
    assert not (state_dir / "pending-autonomous-pr.json").exists()
    assert not (state_dir / "pending-manual-task-owner.json").exists()


def test_recovered_open_pr_is_persisted_before_exact_sha_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    head_sha = "a" * 40
    base_sha = "b" * 40
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(shared_state_tmp_path / "state"))
    current = {
        "number": 79,
        "state": "open",
        "merged": False,
        "draft": False,
        "html_url": "https://github.com/owner/repository/pull/79",
        "user": {"login": "github-actions[bot]"},
        "head": {
            "ref": "codex/local-engineering-79-1-task-1",
            "sha": head_sha,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main", "sha": base_sha},
    }

    def fake_api(token, repo, method, path, payload=None):
        if method == "GET" and path == "pulls/79":
            return current
        if method == "GET" and path == "branches/main":
            return {"commit": {"sha": base_sha}}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(cycle, "api", fake_api)

    def dispatch_after_owner_record(*args):
        pending = cycle.load_pending_pr()
        assert pending["number"] == 79
        assert pending["head_sha"] == head_sha

    monkeypatch.setattr(cycle, "dispatch_and_wait_all", dispatch_after_owner_record)

    result = cycle.complete_pending_pr(
        "token",
        "owner/repository",
        {
            "number": 79,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "branch": "codex/local-engineering-79-1-task-1",
        },
        "79",
        1,
    )

    assert result["validated"] is True
    assert result["awaiting_review"] is True



def test_autonomous_windows_workflows_do_not_pin_tools_to_a_specific_user_or_python_install():
    root = Path(__file__).resolve().parents[1]
    workflow_paths = (
        root / ".github" / "workflows" / "autonomous-codex-engineering.yml",
        root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml",
    )
    for workflow_path in workflow_paths:
        content = workflow_path.read_text(encoding="utf-8")
        lowered = content.casefold()
        assert r"c:\users\admin" not in lowered
        assert r"c:\program files\python311" not in lowered
        assert "get-command ollama" not in lowered
        assert "autonomousollamaruntime.psm1" in lowered

        if workflow_path.name == "autonomous-codex-engineering.yml":
            assert "bootstrap.ps1" in lowered
            assert "paa_venv_dir" in lowered
            assert "actions/setup-python@" not in lowered
        else:
            assert "get-command python" in lowered

def test_persist_run_diagnostic_appends_redacted_report_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(cycle, "_autonomous_state_dir", lambda: tmp_path)
    correlation_id = "c" * 32
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CORRELATION_ID", correlation_id)
    monkeypatch.setenv("GITHUB_JOB", "codex")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_JOB_ID", "987654321")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_STEP_ID", "autonomous-engineering-cycle")

    assert cycle.persist_run_diagnostic(
        "12345", "1", task=1, attempt=2,
        stage="codex_decision", category="task_blocked",
        summary="Codex marked task 1 as blocked.",
        detail="The sandbox policy rejected the requested operation. Bearer bearer-secret-value ghp_0123456789abcdefghijklmnopqrstuvwxyz password=hunter2 OPENAI_API_KEY=sk-test-value CLIENT_SECRET=client-secret-value",
    )
    assert cycle.persist_run_diagnostic(
        "12345", "1", task=1, attempt=2,
        stage="controller", category="RuntimeError",
        summary="Codex reported task 1 as blocked.",
    )

    saved = json.loads((tmp_path / "run-diagnostics" / "12345.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["run_id"] == "12345"
    assert saved["correlation_id"] == correlation_id
    assert saved["diagnostics"][0]["correlation_id"] == correlation_id
    assert saved["diagnostics"][0]["job"] == "codex"
    assert saved["diagnostics"][0]["job_id"] == 987654321
    assert saved["diagnostics"][0]["step_id"] == "autonomous-engineering-cycle"
    assert len(saved["diagnostics"]) == 1
    detail = saved["diagnostics"][0]["detail"]
    assert "sandbox policy rejected" in detail
    assert "bearer-secret-value" not in detail
    assert "ghp_0123456789abcdefghijklmnopqrstuvwxyz" not in detail
    assert "hunter2" not in detail
    assert "sk-test-value" not in detail
    assert "client-secret-value" not in detail
    assert saved["diagnostics"][0]["stage"] == "codex_decision"


def test_persist_run_diagnostic_rejects_non_numeric_run_identifiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(cycle, "_autonomous_state_dir", lambda: tmp_path)

    assert not cycle.persist_run_diagnostic(
        "../123", "1", task=1, attempt=1,
        stage="controller", category="RuntimeError", summary="invalid identity",
    )
    assert not (tmp_path / "run-diagnostics").exists()

def test_marker_only_task_blocked_retries_same_task_and_persists_real_explanation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Complete the selected task.", encoding="utf-8")
    state_root = shared_state_tmp_path / "autonomous-state"
    monkeypatch.setattr(cycle, "ROOT", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repository")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(state_root))
    monkeypatch.setattr(cycle, "start_checkpoint_watcher", lambda *args: None)
    stopped = []
    monkeypatch.setattr(cycle, "stop_checkpoint_watcher", lambda *args: stopped.append(args))
    monkeypatch.setattr(cycle, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle, "snapshot", lambda *args: None)
    monkeypatch.setattr(cycle, "api", lambda *args, **kwargs: [])
    monkeypatch.setattr(cycle, "find_open_autonomous_pr", lambda *args: None)
    monkeypatch.setattr(cycle, "restore_checkpoint", lambda *args: False)
    monkeypatch.setattr(
        cycle, "checked",
        lambda command, **kwargs: (
            "base-sha" if command[:3] == ["git", "rev-parse", "HEAD"]
            else "codex/local-engineering-123-1-task-1" if command[1:3] == ["branch", "--show-current"]
            else ""
        ),
    )
    monkeypatch.setattr(
        cycle, "clear_checkpoint",
        lambda *args: (_ for _ in ()).throw(AssertionError("blocked task checkpoint must not be cleared")),
    )
    calls = []

    def fake_run(command, *, env=None, capture=False, timeout_seconds=None):
        report = Path(command[command.index("--report") + 1])
        calls.append(command)
        if len(calls) == 1:
            report.write_text("TASK_BLOCKED\n", encoding="utf-8")
        else:
            report.write_text(
                "Attempted action: inspected the checkpoint reader and ran the focused broker regression test.\n"
                "Evidence: the reader returned a checkpoint summary whose patch byte count did not match the persisted metadata.\n"
                "Concrete blocker: the selected checkpoint cannot be safely restored until the metadata and patch are reconciled.\n"
                "Safe alternative: preserve the patch and metadata, then rebuild the summary pointer from a verified snapshot.\n"
                "Required to unblock: run the checkpoint integrity repair and repeat the restore validation.\n"
                "TASK_BLOCKED\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cycle, "run", fake_run)
    args = type("Args", (), {
        "run_id": "123", "run_attempt": "1", "model": "test-model",
        "prompt": str(prompt), "mode": "continuous",
    })()

    with pytest.raises(RuntimeError, match="Codex reported task 1 as blocked"):
        cycle.run_cycle(args)

    assert len(calls) == 2
    assert len(stopped) == 2
    retry_prompt = tmp_path / "codex-retry-prompt-1-2.md"
    retry_text = retry_prompt.read_text(encoding="utf-8")
    assert "TASK_BLOCKED is not accepted without a substantive explanation" in retry_text
    assert "Continue the SAME task" in retry_text
    assert "concrete evidence" in retry_text
    diagnostic_path = state_root / "run-diagnostics" / "123.json"
    saved = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    entries = saved["diagnostics"]
    assert [entry["category"] for entry in entries] == ["invalid_blocked_report", "task_blocked"]
    assert "Concrete blocker" in entries[-1]["detail"]
    assert "Concrete blocker" in entries[-1]["summary"]
    assert prompt.read_text(encoding="utf-8") == "Complete the selected task."
