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
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path / "autonomous-state"))
    monkeypatch.setattr(cycle, "start_checkpoint_watcher", lambda *args: None)
    monkeypatch.setattr(cycle, "stop_checkpoint_watcher", lambda *args: None)
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
        }, "102", 1)


def test_run_cycle_recovers_open_task_pr_without_waiting_for_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    records = []
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path / "autonomous-state"))
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

    monkeypatch.setattr(checkpoint, "write_snapshot", fail_snapshot)

    with pytest.raises(RuntimeError, match="simulated snapshot failure"):
        checkpoint.watch("a" * 40, "104", 1, 5)

    assert "simulated snapshot failure" in (state / "worktree-checkpoint-104-1.error").read_text(encoding="utf-8")
    assert not (state / "worktree-checkpoint-104-1.ready").exists()


def test_checkpoint_watcher_real_process_publishes_run_scoped_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
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
    state = tmp_path / "checkpoint-state"
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(state))
    env = os.environ.copy()
    env["AUTONOMOUS_ENGINEERING_PROGRESS_DIR"] = str(state)
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
        assert metadata["schema_version"] == 3
        assert metadata["base_sha"] == base_sha
        assert metadata["run_id"] == run_id
        assert metadata["task"] == task
        assert metadata["watcher_pid"] == process.pid
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

    state = tmp_path / "ProgramData"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)

    def prepare_snapshot(base_sha, run_id, task, watcher_pid):
        paths = checkpoint.paths(run_id, task)
        state.mkdir(parents=True, exist_ok=True)
        metadata = build_checkpoint_metadata(
            base_sha=base_sha,
            run_id=run_id,
            task=task,
            watcher_pid=watcher_pid,
            updated_at=1.0,
            patch_bytes=0,
        )
        checkpoint._atomic_write(
            paths["metadata"], json.dumps(metadata, sort_keys=True) + "\n"
        )
        paths["stop"].write_text("stop\n", encoding="utf-8")

    monkeypatch.setattr(checkpoint, "write_snapshot", prepare_snapshot)
    assert checkpoint.watch("a" * 40, "106", 1, 5) == 0

    metadata = json.loads(
        (state / "worktree-checkpoint-106-1.json").read_text(encoding="utf-8")
    )
    assert metadata["watcher_ready"] is True
    assert metadata["watcher_pid"] == __import__("os").getpid()
    assert not (state / "worktree-checkpoint-106-1.ready").exists()
    assert not (state / "worktree-checkpoint-106-1.error").exists()

def test_checkpoint_watcher_cannot_lose_a_fast_readiness_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import json
    import os
    from types import SimpleNamespace
    from scripts import autonomous_engineering_checkpoint as checkpoint

    state = tmp_path / "ProgramData" / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)
    state.mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
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
                    "schema_version": 3,
                    "watcher_ready": True,
                    "run_id": "105",
                    "task": 1,
                    "base_sha": "a" * 40,
                    "watcher_pid": 4321,
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

    assert cycle.start_checkpoint_watcher("a" * 40, "105", 1) is not None


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
    monkeypatch.setattr(cycle, "clear_checkpoint", lambda *args: None)

    result = cycle.complete_pending_pr("token", "owner/repository", {
        "number": 77,
        "url": "https://github.com/owner/repository/pull/77",
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": "codex/local-engineering-77-1-task-1",
        "draft": True,
        "node_id": "PR_node",
    }, "77", 1)

    assert result["merged"] is True
    assert result["merge_sha"] == merge_sha
    assert len(validations) == 1
    assert any(call[0] == "PUT" and call[1] == "pulls/77/merge" for call in calls)



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


def test_repository_execution_lock_rejects_a_second_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import os
    import sys
    from scripts import autonomous_engineering_cycle as cycle

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    import json
    import os
    import sys
    from types import SimpleNamespace
    from scripts import autonomous_engineering_checkpoint as checkpoint
    from scripts import autonomous_engineering_cycle as cycle

    state = tmp_path / "ProgramData" / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setattr(checkpoint, "store_dir", lambda: state)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
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
                    "schema_version": 3, "watcher_ready": True,
                    "run_id": "107", "task": 1, "base_sha": "a" * 40,
                    "watcher_pid": self.pid,
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
    monkeypatch.setattr(cycle.time, "monotonic", lambda: 0.0)

    assert cycle.start_checkpoint_watcher("a" * 40, "107", 1) is not None
    assert captured["command"][0] == str(tmp_path / "python.exe")
    assert captured["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(tmp_path / ".venv" / "Lib" / "site-packages"),
        "existing-python-path",
    ]



def test_windows_codex_command_uses_call_without_nested_cmd_quoting(monkeypatch):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    executable = r"C:\Users\runner-svc\AppData\Roaming\npm\codex.CMD"
    command = progress.codex_command(executable, ["exec", "--json", "-"])

    assert command == [
        r"C:\Windows\System32\cmd.exe", "/d", "/c", "call",
        executable, "exec", "--json", "-",
    ]


def test_codex_arguments_pin_ollama_and_selected_windows_sandbox(monkeypatch):
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "elevated")
    arguments = progress.codex_arguments("gpt-oss:20b", "codex-report.md")

    assert arguments[:7] == [
        "exec", "-c", 'model_provider="ollama"',
        "-c", 'windows.sandbox="elevated"',
        "--oss", "--local-provider",
    ]
    assert arguments[7:9] == ["ollama", "--sandbox"]
    assert arguments[9] == "workspace-write"
    assert arguments[arguments.index("--model") + 1] == "gpt-oss:20b"


def test_codex_arguments_reject_unverified_windows_sandbox_backend(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "danger-full-access")
    with pytest.raises(ValueError, match="must be 'mxc' or 'elevated'"):
        progress.codex_arguments("gpt-oss:20b", "codex-report.md")


def test_run_codex_handles_early_child_exit_without_broken_pipe_traceback(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scripts import autonomous_engineering_progress as progress

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do the task", encoding="utf-8")
    monkeypatch.setattr(progress.shutil, "which", lambda _: r"C:\\npm\\codex.CMD")
    monkeypatch.setattr(progress, "codex_command", lambda executable, arguments: ["cmd.exe", "/d", "/c", "call", executable, *arguments])
    monkeypatch.setattr(progress, "persist", lambda record: True)
    monkeypatch.setattr(progress, "PLAN_FILE", tmp_path / ".agent-progress.json")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path / "progress"))

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


def test_codex_smoke_evidence_requires_real_successful_command_and_final_turn():
    from scripts import autonomous_engineering_progress as progress

    command_started = {"type": "item.started", "item": {
        "id": "command-1",
        "type": "command_execution",
        "command": "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); print('CODEX_WORKSPACE_SANDBOX_OK')",
        "status": "in_progress",
    }}
    events = [
        command_started,
        {"type": "item.completed", "item": {
            "id": "command-1",
            "type": "command_execution",
            "command": "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); print('CODEX_WORKSPACE_SANDBOX_OK')",
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK\\n",
            "exit_code": 0,
            "status": "completed",
        }},
        {"type": "item.completed", "item": {
            "type": "agent_message",
            "text": "OLLAMA_PROVIDER_AND_SANDBOX_OK",
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
    assert evidence["event_types"] == ["item.started", "item.completed", "item.completed", "turn.completed"]
    assert evidence["item_types"] == ["command_execution", "command_execution", "agent_message"]
    assert evidence["command_started_count"] == 1
    assert evidence["command_completed_count"] == 1
    assert evidence["agent_message_count"] == 1
    assert evidence["turn_usage"] == {"input_tokens": 10, "output_tokens": 2}


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
    ],
)
def test_codex_smoke_evidence_rejects_unproven_or_failed_execution(mutation: str):
    from scripts import autonomous_engineering_progress as progress

    command = {
        "type": "item.completed",
        "item": {
            "id": "command-1",
            "type": "command_execution",
            "command": "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); print('CODEX_WORKSPACE_SANDBOX_OK')",
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK\\n",
            "exit_code": 0,
            "status": "completed",
        },
    }
    command_started = {"type": "item.started", "item": {
        "id": "command-1",
        "type": "command_execution",
        "command": "python -c from pathlib import Path; p=Path('.codex-sandbox-probe.tmp'); p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink(); print('CODEX_WORKSPACE_SANDBOX_OK')",
        "status": "in_progress",
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
