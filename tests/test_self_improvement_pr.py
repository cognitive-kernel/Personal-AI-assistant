import hashlib

import pytest

from app import self_improvement


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
