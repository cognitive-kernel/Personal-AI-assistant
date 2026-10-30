import hashlib
import os
import subprocess
import sys
import urllib.request
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
    (root / "app" / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    (root / "docs" / "design.md").write_text("# Design\n", encoding="utf-8")
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
            stdout='{"status":"passed","files_checked":3}\n',
            stderr="",
            duration_ms=12,
        ),
    )


def _patch() -> str:
    return (
        "diff --git a/app/sample.py b/app/sample.py\n"
        "--- a/app/sample.py\n"
        "+++ b/app/sample.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
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
    assert any(event["event_type"] == "approved_for_pr" and event["details"].get("publication") == "pr_opened" for event in self_improvement.proposal_events(proposal["id"]))

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



def test_self_improvement_ui_requires_confirmation_before_opening_draft_pr():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "ایجاد PR پیش‌نویس در GitHub" in page
    assert '"/api/self-improvement/proposals/"+item.id+"/open-pr"' in page
    assert "PR ادغام یا مستقر نمی‌شود" in page
    assert 'JSON.stringify({confirm:true})' in page



def _record_published_pr(proposal, base_sha):
    details = {
        "publication": "pr_opened",
        "pull_request_number": 42,
        "pull_request_url": "https://github.com/owner/repository/pull/42",
        "base_commit_sha": base_sha,
        "base_branch": "main",
        "patch_sha256": hashlib.sha256(_patch().encode("utf-8")).hexdigest(),
        "commit_sha": "e" * 40,
    }
    db.init_databases()
    with learning_connection() as conn:
        self_improvement._append_event(conn, proposal["id"], "approved_for_pr", details)


def test_rollback_pr_requires_original_pr_merged(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    _record_published_pr(proposal, base_sha)
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setattr(
        self_improvement,
        "_github_api",
        lambda method, path, payload=None: {
            "merged": False,
            "merge_commit_sha": None,
        } if method == "GET" and path == "/pulls/42" else {},
    )
    with pytest.raises(self_improvement.ProposalError, match="only be created after the original pull request is merged"):
        self_improvement.open_rollback_pull_request(proposal["id"], confirm=True)


def test_rollback_pr_rejects_diverged_base_history(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    _record_published_pr(proposal, base_sha)
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    merge_sha = "1" * 40
    current_sha = "2" * 40

    def fake_github_api(method, path, payload=None):
        if method == "GET" and path == "/pulls/42":
            return {"merged": True, "merge_commit_sha": merge_sha, "base": {"ref": "main"}}
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": current_sha}}
        if method == "GET" and path == "/compare/" + merge_sha + "..." + current_sha:
            return {"status": "diverged"}
        raise AssertionError("Unexpected GitHub API request: " + method + " " + path)

    monkeypatch.setattr(self_improvement, "_github_api", fake_github_api)
    with pytest.raises(self_improvement.ProposalError, match="not a descendant of the original merge"):
        self_improvement.open_rollback_pull_request(proposal["id"], confirm=True)


def test_rollback_pr_restores_only_unchanged_merged_paths(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    _record_published_pr(proposal, base_sha)
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    calls = []
    merge_sha = "1" * 40
    current_sha = "2" * 40
    tree_before = "3" * 40
    tree_merged = "4" * 40
    tree_current = "5" * 40
    rollback_tree = "6" * 40
    rollback_commit = "7" * 40
    old_blob = "8" * 40
    merged_blob = "9" * 40

    def fake_github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/pulls/42":
            return {"merged": True, "merge_commit_sha": merge_sha, "base": {"ref": "main"}}
        if method == "GET" and path == "/compare/" + merge_sha + "..." + current_sha:
            return {"status": "ahead"}
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": current_sha}}
        if method == "GET" and path == "/git/commits/" + base_sha:
            return {"tree": {"sha": tree_before}}
        if method == "GET" and path == "/git/commits/" + merge_sha:
            return {"tree": {"sha": tree_merged}}
        if method == "GET" and path == "/git/commits/" + current_sha:
            return {"tree": {"sha": tree_current}}
        if method == "GET" and path == "/git/trees/" + tree_before + "?recursive=1":
            return {"truncated": False, "tree": [{"path": "app/sample.py", "type": "blob", "mode": "100644", "sha": old_blob}]}
        if method == "GET" and path == "/git/trees/" + tree_merged + "?recursive=1":
            return {"truncated": False, "tree": [{"path": "app/sample.py", "type": "blob", "mode": "100644", "sha": merged_blob}]}
        if method == "GET" and path == "/git/trees/" + tree_current + "?recursive=1":
            return {"truncated": False, "tree": [{"path": "app/sample.py", "type": "blob", "mode": "100644", "sha": merged_blob}]}
        if method == "POST" and path == "/git/trees":
            assert payload["base_tree"] == tree_current
            assert payload["tree"] == [{"path": "app/sample.py", "mode": "100644", "type": "blob", "sha": old_blob}]
            return {"sha": rollback_tree}
        if method == "POST" and path == "/git/commits":
            assert payload["tree"] == rollback_tree
            assert payload["parents"] == [current_sha]
            return {"sha": rollback_commit}
        if method == "POST" and path == "/git/refs":
            assert payload["sha"] == rollback_commit
            return {"object": {"sha": rollback_commit}}
        if method == "POST" and path == "/pulls":
            assert payload["draft"] is True
            assert payload["base"] == "main"
            return {"number": 43, "html_url": "https://github.com/owner/repository/pull/43"}
        raise AssertionError("Unexpected GitHub API request: " + method + " " + path)

    monkeypatch.setattr(self_improvement, "_github_api", fake_github_api)
    result = self_improvement.open_rollback_pull_request(proposal["id"], confirm=True)
    assert result["pull_request_number"] == 43
    assert result["draft"] is True
    assert result["merged"] is False
    repeated = self_improvement.open_rollback_pull_request(proposal["id"], confirm=True)
    assert repeated["already_opened"] is True
    assert repeated["pull_request_number"] == 43
    assert len([call for call in calls if call[0] == "POST" and call[1] == "/pulls"]) == 1


def test_rollback_pr_refuses_to_overwrite_later_changes(proposal_repo, monkeypatch):
    proposal, base_sha = _approved_proposal(proposal_repo, monkeypatch)
    _record_published_pr(proposal, base_sha)
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    merge_sha = "1" * 40
    current_sha = "2" * 40
    tree_before, tree_merged, tree_current = "3" * 40, "4" * 40, "5" * 40

    def fake_github_api(method, path, payload=None):
        if method == "GET" and path == "/pulls/42":
            return {"merged": True, "merge_commit_sha": merge_sha, "base": {"ref": "main"}}
        if method == "GET" and path == "/compare/" + merge_sha + "..." + current_sha:
            return {"status": "ahead"}
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": current_sha}}
        if method == "GET" and path == "/git/commits/" + base_sha:
            return {"tree": {"sha": tree_before}}
        if method == "GET" and path == "/git/commits/" + merge_sha:
            return {"tree": {"sha": tree_merged}}
        if method == "GET" and path == "/git/commits/" + current_sha:
            return {"tree": {"sha": tree_current}}
        if method == "GET" and path.startswith("/git/trees/"):
            tree_sha = path.split("/git/trees/", 1)[1].split("?", 1)[0]
            blob = "8" * 40 if tree_sha == tree_before else "9" * 40 if tree_sha == tree_merged else "a" * 40
            return {"truncated": False, "tree": [{"path": "app/sample.py", "type": "blob", "mode": "100644", "sha": blob}]}
        raise AssertionError("Unexpected GitHub API request: " + method + " " + path)

    monkeypatch.setattr(self_improvement, "_github_api", fake_github_api)
    with pytest.raises(self_improvement.ProposalError, match="changed after the original PR merge"):
        self_improvement.open_rollback_pull_request(proposal["id"], confirm=True)


def test_rollback_pr_ui_is_explicit_and_advisory():
    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "ایجاد PR پیش‌نویس بازگشت" in page
    assert '"/api/self-improvement/proposals/"+item.id+"/rollback-pr"' in page
    assert "هیچ ادغام یا استقراری انجام نمی‌شود" in page


def test_github_api_disables_environment_proxies_and_redirects(monkeypatch):
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("SELF_IMPROVEMENT_GITHUB_REPOSITORY", "owner/repository")
    captured = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, limit):
            captured["read_limit"] = limit
            return b'{"object":{"sha":"0123456789abcdef"}}'

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(
        self_improvement.urllib.request, "build_opener", build_opener
    )
    result = self_improvement._github_api("GET", "/git/ref/heads/main")

    assert result == {"object": {"sha": "0123456789abcdef"}}
    assert captured["timeout"] == 15
    assert captured["read_limit"] == 1024 * 1024
    request = captured["request"]
    assert request.full_url == "https://api.github.com/repos/owner/repository/git/ref/heads/main"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert any(
        isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in captured["handlers"]
    )
    redirect_handlers = [
        handler for handler in captured["handlers"]
        if isinstance(handler, urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    assert redirect_handlers[0].redirect_request(
        None, None, 302, "Found", {}, "https://evil.example/"
    ) is None
