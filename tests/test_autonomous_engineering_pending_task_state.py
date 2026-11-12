from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.autonomous_engineering_pending_task_state import (
    claim_pending_task_owner_unlocked,
    pending_task_owner_path,
    task_digest,
    validate_pending_task_owner_unlocked,
)
from scripts.autonomous_engineering_state_lock import durable_state_lock


def test_task_owner_is_persisted_validated_and_reclaimed_idempotently(tmp_path: Path):
    task = "Fix recovery ownership"
    with durable_state_lock(tmp_path):
        first = claim_pending_task_owner_unlocked(
            tmp_path, task, "1001", 1, source="manual_dispatch",
        )
        validated = validate_pending_task_owner_unlocked(tmp_path, task, "1001", 1)
        repeated = claim_pending_task_owner_unlocked(
            tmp_path, task, "1001", 1, source="recovery",
        )

    assert validated["owner_run_id"] == "1001"
    assert repeated["claim_count"] == 1
    assert repeated["submitted_run_id"] == "1001"
    assert repeated["source"] == "manual_dispatch"
    assert first == repeated


def test_task_owner_recovery_claim_advances_owner_without_rewriting_submission(tmp_path: Path):
    task = "Recover a previously interrupted run"
    with durable_state_lock(tmp_path):
        original = claim_pending_task_owner_unlocked(
            tmp_path, task, "2001", 1, source="manual_dispatch",
        )
        recovered = claim_pending_task_owner_unlocked(
            tmp_path, task, "2002", 2, source="recovery",
        )
        validated = validate_pending_task_owner_unlocked(tmp_path, task, "2002", 2)

    assert recovered["submitted_run_id"] == original["submitted_run_id"] == "2001"
    assert recovered["owner_run_id"] == validated["owner_run_id"] == "2002"
    assert recovered["owner_attempt"] == 2
    assert recovered["claim_count"] == 2
    assert recovered["created_at"] == original["created_at"]
    assert recovered["claimed_at"] >= original["claimed_at"]


def test_task_owner_rejects_task_mutation_and_preserves_owner_file(tmp_path: Path):
    with durable_state_lock(tmp_path):
        claim_pending_task_owner_unlocked(
            tmp_path, "Original task", "3001", 1, source="manual_dispatch",
        )
        owner_path = pending_task_owner_path(tmp_path)
        before = owner_path.read_bytes()

        with pytest.raises(RuntimeError, match="does not match the task"):
            claim_pending_task_owner_unlocked(
                tmp_path, "Changed task", "3002", 1, source="recovery",
            )

        assert owner_path.read_bytes() == before


def test_task_owner_rejects_corrupt_or_unsupported_records(tmp_path: Path):
    owner_path = pending_task_owner_path(tmp_path)
    owner_path.parent.mkdir(parents=True, exist_ok=True)
    owner_path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    with durable_state_lock(tmp_path):
        with pytest.raises(RuntimeError, match="schema v1"):
            claim_pending_task_owner_unlocked(
                tmp_path, "Task", "4001", 1, source="recovery",
            )


def test_task_owner_requires_valid_run_identity_and_attempt(tmp_path: Path):
    with pytest.raises(ValueError, match="run ID"):
        claim_pending_task_owner_unlocked(
            tmp_path, "Task", "../invalid", 1, source="recovery",
        )
    with pytest.raises(ValueError, match="attempt"):
        claim_pending_task_owner_unlocked(
            tmp_path, "Task", "4002", 0, source="recovery",
        )


@pytest.mark.skipif(os.name != "nt", reason="PowerShell/.NET SHA-256 interoperability is Windows-only")
def test_powershell_and_python_compute_identical_task_digest():
    task = "Repair checkpoint ownership — خطای بازیابی"
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "AutonomousEngineeringTaskState.psm1"
    script = (
        f"Import-Module -Name '{module_path}' -DisableNameChecking; "
        f"Get-AutonomousTaskDigest -Task '{task}'"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == task_digest(task)