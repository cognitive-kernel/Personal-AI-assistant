from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.autonomous_engineering_pending_task_state import (
    claim_pending_task_owner_unlocked,
    reconcile_pending_task_owner_unlocked,
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

        with pytest.raises(RuntimeError, match="does not match task content"):
            claim_pending_task_owner_unlocked(
                tmp_path, "Changed task", "3002", 1, source="recovery",
            )

        assert owner_path.read_bytes() == before


def test_task_owner_replace_failure_preserves_previous_record_and_cleans_temp(
    tmp_path: Path, monkeypatch,
):
    import scripts.autonomous_engineering_pending_task_state as owner_state

    task = "The prior owner record must survive a failed atomic replace."
    with durable_state_lock(tmp_path):
        claim_pending_task_owner_unlocked(
            tmp_path, task, "5101", 1, source="manual_dispatch",
        )
    owner_path = pending_task_owner_path(tmp_path)
    previous_bytes = owner_path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(owner_state.os, "replace", fail_replace)
    with durable_state_lock(tmp_path):
        with pytest.raises(OSError, match="simulated atomic replace failure"):
            claim_pending_task_owner_unlocked(
                tmp_path, task, "5102", 1, source="recovery",
            )

    assert owner_path.read_bytes() == previous_bytes
    assert not list(tmp_path.glob("pending-manual-task-owner.json.*.tmp"))


def test_task_owner_temp_create_collision_preserves_other_process_file(tmp_path: Path, monkeypatch):
    import types
    import scripts.autonomous_engineering_pending_task_state as owner_state

    task = "A colliding temporary path must never be removed by this process."
    with durable_state_lock(tmp_path):
        claim_pending_task_owner_unlocked(
            tmp_path, task, "5201", 1, source="manual_dispatch",
        )

    owner_path = pending_task_owner_path(tmp_path)
    fixed_time = 123456789
    fixed_uuid = "f" * 32
    temporary = owner_path.with_name(
        f"{owner_path.name}.{os.getpid()}.{fixed_time}.{fixed_uuid}.tmp"
    )
    sentinel = b"owned by a different process"
    temporary.write_bytes(sentinel)
    monkeypatch.setattr(owner_state.time, "time_ns", lambda: fixed_time)
    monkeypatch.setattr(owner_state.uuid, "uuid4", lambda: types.SimpleNamespace(hex=fixed_uuid))

    with durable_state_lock(tmp_path):
        with pytest.raises(FileExistsError):
            claim_pending_task_owner_unlocked(
                tmp_path, task, "5202", 1, source="recovery",
            )

    assert temporary.read_bytes() == sentinel


def test_task_owner_rejects_corrupt_or_unsupported_records(tmp_path: Path):
    owner_path = pending_task_owner_path(tmp_path)
    owner_path.parent.mkdir(parents=True, exist_ok=True)
    owner_path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    with durable_state_lock(tmp_path):
        with pytest.raises(RuntimeError, match="declared schema"):
            claim_pending_task_owner_unlocked(
                tmp_path, "Task", "4001", 1, source="recovery",
            )


def test_task_owner_requires_valid_run_identity_and_attempt(tmp_path: Path):
    with pytest.raises(ValueError, match="run ID"):
        claim_pending_task_owner_unlocked(
            tmp_path, "Task", "not-a-run-id", 1, source="recovery",
        )
    with pytest.raises(ValueError, match="attempt"):
        claim_pending_task_owner_unlocked(
            tmp_path, "Task", "4002", 0, source="recovery",
        )
    with pytest.raises(ValueError, match="attempt"):
        claim_pending_task_owner_unlocked(
            tmp_path, "Task", "4003", 2_147_483_648, source="recovery",
        )


def test_task_digest_normalizes_all_supported_newline_styles():
    assert task_digest("first line\nsecond line") == task_digest("first line\r\nsecond line")
    assert task_digest("first line\nsecond line") == task_digest("first line\rsecond line")


def test_owner_record_is_portable_between_windows_and_unix_newlines(tmp_path: Path):
    with durable_state_lock(tmp_path):
        original = claim_pending_task_owner_unlocked(
            tmp_path, "first line\nsecond line", "4501", 1, source="manual_dispatch",
        )
        validated = validate_pending_task_owner_unlocked(
            tmp_path, "first line\r\nsecond line", "4501", 1,
        )

    assert validated == original
    assert validated["task_sha256"] == task_digest("first line\rsecond line")


@pytest.mark.skipif(os.name != "nt", reason="PowerShell/.NET SHA-256 interoperability is Windows-only")
@pytest.mark.parametrize(
    "task",
    [
        "Repair checkpoint ownership — خطای بازیابی",
        "first line\nsecond line",
        "first line\r\nsecond line",
        "first line\rsecond line",
        "first line\r\nsecond line\nthird line\rfourth line",
    ],
)
def test_powershell_and_python_compute_identical_task_digest(task: str):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "AutonomousEngineeringTaskState.psm1"
    escaped = task.replace("`", "``").replace('"', '`"').replace("\r", "`r").replace("\n", "`n")
    script = (
        f"Import-Module '{module_path}'; "
        f'$task = "{escaped}"; '
        "Get-AutonomousTaskDigest -Task $task"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == task_digest(task)


def test_reconcile_refuses_changed_task_and_preserves_owner(tmp_path: Path):
    owner_path = pending_task_owner_path(tmp_path)
    with durable_state_lock(tmp_path):
        claim_pending_task_owner_unlocked(
            tmp_path, "Previous task", "5001", 1, source="manual_dispatch",
        )
        previous_owner = owner_path.read_bytes()

        with pytest.raises(RuntimeError, match="does not match task content"):
            reconcile_pending_task_owner_unlocked(
                tmp_path, "Changed task", "5002", 2, source="recovery",
            )

    assert owner_path.read_bytes() == previous_owner
    assert not list(tmp_path.glob("pending-manual-task-owner.json.quarantine-*.json"))


def test_reconcile_never_quarantines_malformed_owner_when_digest_drifted(tmp_path: Path):
    owner_path = pending_task_owner_path(tmp_path)
    raw = json.dumps({
        "schema_version": 99,
        "task_sha256": task_digest("Previous durable task"),
        "owner_run_id": "5001",
    }).encode("utf-8")
    owner_path.write_bytes(raw)

    with durable_state_lock(tmp_path):
        with pytest.raises(RuntimeError, match="digest does not match task content"):
            reconcile_pending_task_owner_unlocked(
                tmp_path, "Changed durable task", "5002", 1, source="recovery",
            )

    assert owner_path.read_bytes() == raw
    assert not list(tmp_path.glob("pending-manual-task-owner.json.quarantine-*.json"))


def test_reconcile_quarantines_malformed_owner_record(tmp_path: Path):
    owner_path = pending_task_owner_path(tmp_path)
    owner_path.write_text("{malformed", encoding="utf-8")

    with durable_state_lock(tmp_path):
        recovered = reconcile_pending_task_owner_unlocked(
            tmp_path, "Durable task", "5002", 1, source="recovery",
        )

    backups = list(tmp_path.glob("pending-manual-task-owner.json.quarantine-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{malformed"
    assert recovered["task_sha256"] == task_digest("Durable task")
    assert json.loads(owner_path.read_text(encoding="utf-8")) == recovered
