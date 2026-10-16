import os
import subprocess
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
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
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


def _patch() -> str:
    return (
        "diff --git a/app/sample.py b/app/sample.py\n"
        "--- a/app/sample.py\n"
        "+++ b/app/sample.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )


def test_patch_format_allows_only_reviewable_source_paths():
    meta = self_improvement.validate_patch_format(_patch())
    assert meta["files"] == ["app/sample.py"]
    assert meta["file_count"] == 1
    assert len(meta["sha256"]) == 64


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/../outside.py b/../outside.py\n--- a/../outside.py\n+++ b/../outside.py\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/app/secret.txt b/app/secret.txt\n--- a/app/secret.txt\n+++ b/app/secret.txt\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/app/sample.py b/app/other.py\n--- a/app/sample.py\n+++ b/app/other.py\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/app/sample.py b/app/sample.py\nnew file mode 120000\n--- a/app/sample.py\n+++ b/app/sample.py\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/app/sample.py b/app/sample.py\nGIT binary patch\n",
    ],
)
def test_patch_format_rejects_unsafe_or_ambiguous_patches(patch):
    with pytest.raises(self_improvement.ProposalError):
        self_improvement.validate_patch_format(patch)


def test_proposal_validation_and_approval_never_apply_code(proposal_repo):
    source = proposal_repo / "app" / "sample.py"
    before = source.read_text(encoding="utf-8")
    head_before = _git(proposal_repo, "rev-parse", "HEAD")
    proposal = self_improvement.create_proposal(
        "Improve sample behavior", "A small reviewed change with a testable diff.", _patch()
    )
    assert proposal["status"] == "proposed"
    assert proposal["patch"] == _patch()

    validated = self_improvement.validate_proposal(proposal["id"])
    assert validated["status"] == "validated"
    assert validated["validation_report"]["apply_check"] == "passed"
    assert validated["validation_report"]["tests_executed"] is False
    assert validated["validation_report"]["isolated_execution"] is False

    with pytest.raises(self_improvement.ProposalError, match="Explicit confirmation"):
        self_improvement.approve_for_pr(proposal["id"], confirm=False)
    approved = self_improvement.approve_for_pr(proposal["id"], confirm=True)
    assert approved["status"] == "approved_for_pr"
    assert source.read_text(encoding="utf-8") == before
    assert _git(proposal_repo, "rev-parse", "HEAD") == head_before
    assert [event["event_type"] for event in self_improvement.proposal_events(proposal["id"])] == [
        "proposed", "validated", "approved_for_pr"
    ]


def test_validation_fails_closed_when_repository_head_changes(proposal_repo):
    proposal = self_improvement.create_proposal(
        "Stale proposal", "Must not validate against a changed base.", _patch()
    )
    (proposal_repo / "docs" / "design.md").write_text("# Changed\n", encoding="utf-8")
    _git(proposal_repo, "add", "docs/design.md")
    _git(proposal_repo, "commit", "-qm", "move base")
    with pytest.raises(self_improvement.ProposalError, match="HEAD changed"):
        self_improvement.validate_proposal(proposal["id"])
    saved = self_improvement.get_proposal(proposal["id"])
    assert saved["status"] == "proposed"


def test_rejection_requires_confirmation_and_is_audited(proposal_repo):
    proposal = self_improvement.create_proposal(
        "Reject me", "Test explicit rejection.", _patch()
    )
    with pytest.raises(self_improvement.ProposalError, match="Explicit confirmation"):
        self_improvement.reject_proposal(proposal["id"], confirm=False)
    rejected = self_improvement.reject_proposal(
        proposal["id"], confirm=True, reason="Not worth the risk."
    )
    assert rejected["status"] == "rejected"
    assert self_improvement.proposal_events(proposal["id"])[-1]["details"]["reason"] == "Not worth the risk."
