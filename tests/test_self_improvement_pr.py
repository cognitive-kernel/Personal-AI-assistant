import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import db, self_improvement
from app.db import learning_connection


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout.strip()


@pytest.fixture
def proposal_repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "app" / "sample.py").write_text("value = 1\\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text("def test_value():\\n    assert True\\n", encoding="utf-8")
    (root / "docs" / "design.md").write_text("# Design\\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Self Improvement Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", "app", "tests", "docs")
    _git(root, "commit", "-qm", "baseline")
    monkeypatch.setenv("SELF_IMPROVEMENT_REPO_ROOT", str(root))
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return root


def _enable_static_check(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", sys.executable)
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.11-slim@sha256:" + "b" * 64)
    monkeypatch.setenv("SANDBOX_TEST_IMAGE", "sha256:" + "c" * 64)
    monkeypatch.setattr(self_improvement.sandbox_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(
        self_improvement.sandbox,
        "run_workspace_tests",
        lambda *args, **kwargs: self_improvement.sandbox.SandboxResult(
            status="succeeded", exit_code=0, stdout="5 passed", stderr="", duration_ms=120
        ),
    )
    monkeypatch.setattr(
        self_improvement.sandbox,
        "run_workspace_syntax_check",
        lambda *args, **kwargs: self_improvement.sandbox.SandboxResult(
            status="succeeded",
            exit_code=0,
            stdout='{"status":"passed","files_checked":3}\\n',
            stderr="",
            duration_ms=12,
        ),
    )


def _patch() -> str:
    return (
        "diff --git a/app/sample.py b/app/sample.py\\n"
        "--- a/app/sample.py\\n"
        "+++ b/app/sample.py\\n"
        "@@ -1 +1 @@\\n"
        "-value = 1\\n"
        "+value = 2\\n"
    )


def _approved_proposal(proposal_repo, monkeypatch):
    proposal = self_improvement.create_proposal(
        "Improve sample behavior", "Prepare a review-only change.", _patch()
    )
    self_improvement.validate_proposal(proposal["id"])
    _enable_static_check(monkeypatch)
    checked = self_improvement.static_check_proposal(proposal["id"], confirm=True)
    assert checked["static_check"]["status"] == "passed"
    approved = self_improvement.approve_for_pr(proposal["id"], confirm=True)
    return approved, _git(proposal_repo, "rev-parse", "HEAD")


def test_open_pr_requires_explicit_confirmation_and_configuration(proposal_repo, monkeypatch):
    proposal, _ = _approved_proposal(proposal_repo, monkeypatch)
    monkeypatch.delenv("SELF_IMPROVEMENT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", raising=False)

    with pytest.raises(self_improvement.ProposalError, match="Explicit confirmation"):
        self_improvement.open_review_pull_request(proposal["id"], confirm=False)
    with pytest.raises(self_improvement.ProposalError, match="SELF_IMPROVEMENT_GITHUB_REPOSITORY"):
        self_improvement.open_review_pull_request(proposal["id"], confirm=True)


def test_open_pr_creates_draft_branch_without_mutating_local_checkout(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    source = proposal_repo / "app" / "sample.py"
    before = source.read_text(encoding="utf-8")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token-never-sent-to-git")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    calls = []
    created_commit = "a" * 40
    created_tree = "b" * 40
    blob_sha = "c" * 40

    def fake_github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": base_sha}}
        if method == "GET" and path == "/git/commits/" + base_sha:
            return {"tree": {"sha": "d" * 40}}
        if method == "POST" and path == "/git/blobs":
            assert payload["encoding"] == "base64"
            return {"sha": blob_sha}
        if method == "POST" and path == "/git/trees":
            assert payload["base_tree"] == "d" * 40
            assert payload["tree"][0]["path"] == "app/sample.py"
            return {"sha": created_tree}
        if method == "POST" and path == "/git/commits":
            assert payload["parents"] == [base_sha]
            assert payload["tree"] == created_tree
            return {"sha": created_commit}
        if method == "POST" and path == "/git/refs":
            assert payload["sha"] == created_commit
            return {"object": {"sha": created_commit}}
        if method == "POST" and path == "/pulls":
            assert payload["draft"] is True
            assert payload["base"] == "main"
            assert payload["head"].startswith("ai-proposal/" + str(proposal["id"]) + "-")
            return {"number": 42, "html_url": "https://github.com/owner/repository/pull/42"}
        raise AssertionError("Unexpected GitHub API request: " + method + " " + path)

    monkeypatch.setattr(self_improvement, "_github_api", fake_github_api)
    result = self_improvement.open_review_pull_request(proposal["id"], confirm=True)

    assert result["pull_request_number"] == 42
    assert result["draft"] is True
    assert result["merged"] is False
    assert result["deployed"] is False
    assert result["patch_sha256"] == hashlib.sha256(_patch().encode()).hexdigest()
    assert source.read_text(encoding="utf-8") == before
    assert _git(proposal_repo, "rev-parse", "HEAD") == base_sha
    assert any(event["event_type"] == "pr_opened" for event in self_improvement.proposal_events(proposal["id"]))

    repeated = self_improvement.open_review_pull_request(proposal["id"], confirm=True)
    assert repeated["already_opened"] is True
    assert repeated["pull_request_number"] == 42
    assert len([call for call in calls if call[0] == "POST" and call[1] == "/pulls"]) == 1


def test_open_pr_rejects_remote_base_drift(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setattr(
        self_improvement,
        "_github_api",
        lambda method, path, payload=None: {"object": {"sha": "f" * 40}}
        if method == "GET" and path == "/git/ref/heads/main"
        else {},
    )
    with pytest.raises(self_improvement.ProposalError, match="Remote base branch has moved"):
        self_improvement.open_review_pull_request(proposal["id"], confirm=True)
    assert _git(proposal_repo, "rev-parse", "HEAD") == base_sha
