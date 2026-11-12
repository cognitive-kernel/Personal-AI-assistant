from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.autonomous_engineering_pending_pr_state import (
    clear_pending_state,
    load_pending_pr,
    recover_pending_state_cleanup_unlocked,
)
from scripts.autonomous_engineering_state_lock import durable_state_lock


def _seed_pending_state(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "pending-autonomous-pr.json").write_text("{}", encoding="utf-8")
    (state_dir / "pending-manual-task.txt").write_text("accepted task", encoding="utf-8")
    (state_dir / "pending-manual-task-owner.json").write_text("{}", encoding="utf-8")


def test_interrupted_post_merge_cleanup_resumes_idempotently(tmp_path: Path):
    _seed_pending_state(tmp_path)
    marker = {
        "schema_version": 1,
        "operation": "clear_after_verified_post_merge",
        "run_id": "5001",
        "created_at": time.time(),
    }
    (tmp_path / "pending-state-cleanup.json").write_text(json.dumps(marker), encoding="utf-8")

    with durable_state_lock(tmp_path):
        assert recover_pending_state_cleanup_unlocked(tmp_path) is True
        assert recover_pending_state_cleanup_unlocked(tmp_path) is False

    assert not (tmp_path / "pending-autonomous-pr.json").exists()
    assert not (tmp_path / "pending-manual-task.txt").exists()
    assert not (tmp_path / "pending-manual-task-owner.json").exists()
    assert not (tmp_path / "pending-state-cleanup.json").exists()


def test_malformed_cleanup_marker_preserves_all_pending_state(tmp_path: Path):
    _seed_pending_state(tmp_path)
    (tmp_path / "pending-state-cleanup.json").write_text("{broken", encoding="utf-8")

    with durable_state_lock(tmp_path):
        with pytest.raises(RuntimeError, match="malformed"):
            recover_pending_state_cleanup_unlocked(tmp_path)

    assert (tmp_path / "pending-autonomous-pr.json").exists()
    assert (tmp_path / "pending-manual-task.txt").read_text(encoding="utf-8") == "accepted task"
    assert (tmp_path / "pending-manual-task-owner.json").exists()


def test_clear_pending_state_commits_and_completes_cleanup_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _seed_pending_state(tmp_path)
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))

    clear_pending_state()

    assert load_pending_pr() is None
    assert not (tmp_path / "pending-manual-task.txt").exists()
    assert not (tmp_path / "pending-manual-task-owner.json").exists()
    assert not (tmp_path / "pending-state-cleanup.json").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/owner/repository/pull/42",
        "https://github.com/owner/repository/pull/43",
        "https://github.com/owner/repository/pull/42?redirect=evil",
        "https://github.com/owner/repository/pull/42#fragment",
    ],
)
def test_pending_pr_loader_rejects_noncanonical_or_mismatched_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
    record = {
        "number": 42,
        "url": url,
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-123-1-task-1",
        "run_id": "123",
        "task": 1,
    }
    path = tmp_path / "pending-autonomous-pr.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(RuntimeError, match="durable contract"):
        load_pending_pr()
