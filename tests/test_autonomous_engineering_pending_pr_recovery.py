from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.autonomous_engineering_pending_pr_recovery import (
    reconcile_pending_pr_state_unlocked,
)
from scripts.autonomous_engineering_state_lock import durable_state_lock


REPOSITORY = "cognitive-kernel/Personal-AI-assistant"


def _controller_pr(number: int = 77, *, state: str = "open", merged: bool = False):
    branch = "codex/local-engineering-123456-2-task-3"
    return {
        "number": number,
        "html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
        "state": state,
        "merged": merged,
        "user": {"login": "github-actions[bot]"},
        "head": {
            "ref": branch,
            "sha": "a" * 40,
            "repo": {"full_name": REPOSITORY},
        },
        "base": {"ref": "main", "sha": "b" * 40},
    }


def test_stale_placeholder_pr_record_is_quarantined_not_treated_as_live_pr(tmp_path: Path):
    path = tmp_path / "pending-autonomous-pr.json"
    original = json.dumps({
        "number": 13,
        "url": "https://github.com/owner/repository/pull/13",
        "head_sha": "c" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-1-1-task-1",
        "run_id": "123",
        "task": 1,
    })
    path.write_text(original, encoding="utf-8")

    with durable_state_lock(tmp_path):
        result = reconcile_pending_pr_state_unlocked(
            tmp_path,
            REPOSITORY,
            [],
            lambda number: {
                "number": number,
                "state": "closed",
                "merged": False,
                "user": {"login": "cognitive-kernel"},
                "head": {
                    "ref": "fix/unrelated",
                    "sha": "d" * 40,
                    "repo": {"full_name": REPOSITORY},
                },
                "base": {"ref": "main", "sha": "e" * 40},
            },
        )

    assert result is None
    assert not path.exists()
    backups = list(tmp_path.glob("pending-autonomous-pr.json.quarantine-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_invalid_pr_record_is_rebuilt_from_unique_open_controller_pr(tmp_path: Path):
    path = tmp_path / "pending-autonomous-pr.json"
    path.write_text("{invalid", encoding="utf-8")
    live = _controller_pr()

    with durable_state_lock(tmp_path):
        result = reconcile_pending_pr_state_unlocked(
            tmp_path, REPOSITORY, [live], lambda number: live,
        )

    assert result == {
        "number": 77,
        "url": f"https://github.com/{REPOSITORY}/pull/77",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "branch": "codex/local-engineering-123456-2-task-3",
        "run_id": "123456",
        "task": 3,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == result


def test_closed_merged_pr_can_be_recovered_from_github_authority(tmp_path: Path):
    path = tmp_path / "pending-autonomous-pr.json"
    path.write_text('{"number": 77}', encoding="utf-8")
    live = _controller_pr(state="closed", merged=True)

    with durable_state_lock(tmp_path):
        result = reconcile_pending_pr_state_unlocked(
            tmp_path, REPOSITORY, [], lambda number: live,
        )

    assert result is not None
    assert result["number"] == 77
    assert result["run_id"] == "123456"
    assert result["task"] == 3


def test_multiple_open_controller_prs_fail_closed_and_preserve_state(tmp_path: Path):
    path = tmp_path / "pending-autonomous-pr.json"
    original = "{invalid"
    path.write_text(original, encoding="utf-8")
    first = _controller_pr(77)
    second = _controller_pr(78)

    with durable_state_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Multiple open autonomous PRs"):
            reconcile_pending_pr_state_unlocked(
                tmp_path, REPOSITORY, [first, second], lambda number: first,
            )

    assert path.read_text(encoding="utf-8") == original
