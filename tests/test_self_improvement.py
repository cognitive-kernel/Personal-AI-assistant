import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
            status="succeeded",
            exit_code=0,
            stdout="5 passed",
            stderr="",
            duration_ms=120,
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


def test_proposal_validation_and_approval_never_apply_code(proposal_repo, monkeypatch):
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

    with pytest.raises(self_improvement.ProposalError, match="isolated syntax check"):
        self_improvement.approve_for_pr(proposal["id"], confirm=True)
    _enable_static_check(monkeypatch)
    with pytest.raises(self_improvement.ProposalError, match="Explicit confirmation"):
        self_improvement.static_check_proposal(proposal["id"], confirm=False)
    checked = self_improvement.static_check_proposal(proposal["id"], confirm=True)
    assert checked["static_check"]["status"] == "passed"
    assert checked["static_check"]["tests_executed"] is True
    assert checked["static_check"]["candidate_code_executed"] is True
    assert checked["static_check"]["behavioral_tests"]["status"] == "succeeded"
    approved = self_improvement.approve_for_pr(proposal["id"], confirm=True)
    assert approved["status"] == "approved_for_pr"
    assert source.read_text(encoding="utf-8") == before
    assert _git(proposal_repo, "rev-parse", "HEAD") == head_before
    assert [event["event_type"] for event in self_improvement.proposal_events(proposal["id"])] == [
        "proposed", "validated", "static_checked", "approved_for_pr"
    ]



def test_failed_behavior_tests_block_proposal_approval(proposal_repo, monkeypatch):
    proposal = self_improvement.create_proposal(
        "Failing candidate", "Behavioral tests must pass before approval.", _patch()
    )
    self_improvement.validate_proposal(proposal["id"])
    _enable_static_check(monkeypatch)
    monkeypatch.setattr(
        self_improvement.sandbox,
        "run_workspace_tests",
        lambda *args, **kwargs: self_improvement.sandbox.SandboxResult(
            status="failed", exit_code=1, stdout="1 failed", stderr="", duration_ms=80
        ),
    )

    checked = self_improvement.static_check_proposal(proposal["id"], confirm=True)

    assert checked["static_check"]["status"] == "failed"
    assert checked["static_check"]["behavioral_tests"]["status"] == "failed"
    assert checked["static_check"]["tests_executed"] is True
    with pytest.raises(self_improvement.ProposalError, match="behavior test run"):
        self_improvement.approve_for_pr(proposal["id"], confirm=True)



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



def test_self_improvement_api_is_loopback_guarded_and_uses_explicit_approval(
    proposal_repo, tmp_path, monkeypatch
):
    from app import cloud_resources
    from app.main import app
    from app.task_routes import require_loopback_client

    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )
    app.dependency_overrides[require_loopback_client] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/self-improvement/proposals",
                json={
                    "title": "API proposal",
                    "rationale": "Verify local proposal lifecycle.",
                    "patch": _patch(),
                },
            )
            assert response.status_code == 201, response.text
            proposal_id = response.json()["proposal"]["id"]
            assert client.post(
                f"/api/self-improvement/proposals/{proposal_id}/validate"
            ).status_code == 200
            _enable_static_check(monkeypatch)
            checked = client.post(
                f"/api/self-improvement/proposals/{proposal_id}/static-check",
                json={"confirm": True},
            )
            assert checked.status_code == 200, checked.text
            assert checked.json()["proposal"]["static_check"]["status"] == "passed"
            denied = client.post(
                f"/api/self-improvement/proposals/{proposal_id}/approve",
                json={"confirm": False},
            )
            assert denied.status_code == 409
            approved = client.post(
                f"/api/self-improvement/proposals/{proposal_id}/approve",
                json={"confirm": True},
            )
            assert approved.status_code == 200
            assert approved.json()["proposal"]["status"] == "approved_for_pr"
    finally:
        app.dependency_overrides.pop(require_loopback_client, None)



def test_self_improvement_ui_discloses_non_execution_and_explicit_approval():
    app_root = Path(__file__).parents[1] / "app"
    page = (app_root / "static" / "index.html").read_text(encoding="utf-8") + "\n" + (app_root / "dashboard_sections" / "self-improvement.html").read_text(encoding="utf-8")
    assert 'id="self-improvement-section"' in page
    assert "SELF_IMPROVEMENT_REPO_ROOT" in page
    assert "کد پیشنهادی فقط داخل کانتینر اجرا می‌شود" in page
    assert "آزمون‌های رفتاری را در کانتینر Docker ایزوله اجرا می‌کند" in page
    assert "تأیید برای بررسی PR" in page
    assert '"/api/self-improvement/proposals/"+item.id+"/validate"' in page
    assert '"/api/self-improvement/proposals/"+item.id+"/static-check"' in page
    assert '"/api/self-improvement/proposals/"+item.id+"/approve"' in page



def test_test_integrity_guard_rejects_removed_assertions(tmp_path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    (baseline / "tests").mkdir(parents=True)
    (candidate / "tests").mkdir(parents=True)
    original = "def test_contract():\n    assert value == 1\n    assert other == 2\n"
    weakened = "def test_contract():\n    assert value == 1\n"
    (baseline / "tests" / "test_contract.py").write_text(original, encoding="utf-8")
    (candidate / "tests" / "test_contract.py").write_text(weakened, encoding="utf-8")

    report = self_improvement._test_integrity_report(baseline, candidate)

    assert report["status"] == "failed"
    assert report["weakened_signals"][0]["reason"] == "test_assertions_or_exception_checks_reduced"
    assert report["tests_executed"] is False


def test_test_integrity_guard_rejects_added_skip_marker(tmp_path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    (baseline / "tests").mkdir(parents=True)
    (candidate / "tests").mkdir(parents=True)
    original = "import pytest\ndef test_contract():\n    assert value == 1\n"
    weakened = "import pytest\n@pytest.mark.skip\ndef test_contract():\n    assert value == 1\n"
    (baseline / "tests" / "test_contract.py").write_text(original, encoding="utf-8")
    (candidate / "tests" / "test_contract.py").write_text(weakened, encoding="utf-8")

    report = self_improvement._test_integrity_report(baseline, candidate)

    assert report["status"] == "failed"
    assert any(item["reason"] == "skip_or_xfail_markers_increased" for item in report["weakened_signals"])


def test_test_integrity_guard_accepts_additive_test_coverage(tmp_path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    (baseline / "tests").mkdir(parents=True)
    (candidate / "tests").mkdir(parents=True)
    original = "def test_contract():\n    assert value == 1\n"
    additive = original + "\ndef test_additional_contract():\n    assert value == 2\n"
    (baseline / "tests" / "test_contract.py").write_text(original, encoding="utf-8")
    (candidate / "tests" / "test_contract.py").write_text(additive, encoding="utf-8")

    report = self_improvement._test_integrity_report(baseline, candidate)

    assert report["status"] == "passed"
    assert report["candidate_asserts"] > report["baseline_asserts"]
