from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scripts.autonomous_engineering_pending_task_state import (
    claim_pending_task_owner_unlocked,
    task_digest,
    validate_pending_task_owner_unlocked,
)


WORKFLOW_PATH = ".github/workflows/autonomous-codex-engineering.yml"


def _powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def test_schema_v2_owner_requires_and_accepts_rebind_audit_fields(tmp_path: Path):
    task = "The durable task text remains unchanged."
    now = time.time()
    record = {
        "schema_version": 2,
        "task_sha256": task_digest(task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 1,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 2,
        "source": "recovery",
        "previous_task_sha256": "a" * 64,
        "rebind_reason": "owner_authorized_task_content_reconciliation",
        "rebind_authorization_run_id": "200",
        "rebind_authorization_run_attempt": 1,
        "rebound_at": now,
    }
    owner_path = tmp_path / "pending-manual-task-owner.json"
    owner_path.write_text(json.dumps(record), encoding="utf-8")

    validated = validate_pending_task_owner_unlocked(tmp_path, task, "200", 1)

    assert validated == record
    claimed = claim_pending_task_owner_unlocked(tmp_path, task, "300", 2, source="recovery")
    assert claimed["schema_version"] == 2
    assert claimed["owner_run_id"] == "300"
    assert claimed["rebind_authorization_run_id"] == "200"
    assert claimed["previous_task_sha256"] == record["previous_task_sha256"]
    assert claimed["claim_count"] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record, task: record.update(previous_task_sha256=record["task_sha256"]),
        lambda record, task: record.update(source="manual_dispatch"),
        lambda record, task: record.update(rebind_authorization_run_id="not-a-run-id"),
        lambda record, task: record.update(rebind_authorization_run_attempt=0),
        lambda record, task: record.update(owner_run_id="not-a-run-id"),
        lambda record, task: record.update(submitted_run_id="not-a-run-id"),
        lambda record, task: record.update(schema_version=2.0),
        lambda record, task: record.update(claim_count=2_147_483_648),
    ],
    ids=[
        "previous-digest-must-differ",
        "schema-v2-is-recovery-only",
        "authorization-id-must-be-numeric",
        "authorization-attempt-must-be-positive",
        "owner-id-must-be-numeric",
        "schema-version-must-be-integer",
        "claim-count-must-fit-powershell-int32",
    ],
)
def test_schema_v2_owner_rejects_inconsistent_audit_fields(
    tmp_path: Path, mutation,
):
    task = "The durable task text remains unchanged."
    now = time.time()
    record = {
        "schema_version": 2,
        "task_sha256": task_digest(task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 1,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 2,
        "source": "recovery",
        "previous_task_sha256": "a" * 64,
        "rebind_reason": "owner_authorized_task_content_reconciliation",
        "rebind_authorization_run_id": "200",
        "rebound_at": now,
    }
    mutation(record, task)
    owner_path = tmp_path / "pending-manual-task-owner.json"
    owner_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(RuntimeError):
        validate_pending_task_owner_unlocked(tmp_path, task, "200", 1)


def test_schema_v2_owner_claim_can_advance_without_rewriting_rebind_audit(tmp_path: Path):
    task = "The task is still the same after a recovery claim."
    now = time.time()
    digest = task_digest(task)
    record = {
        "schema_version": 2,
        "task_sha256": digest,
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 2,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 2,
        "source": "recovery",
        "previous_task_sha256": "a" * 64,
        "rebind_reason": "owner_authorized_task_content_reconciliation",
        "rebind_authorization_run_id": "200",
        "rebind_authorization_run_attempt": 1,
        "rebound_at": now,
    }
    owner_path = tmp_path / "pending-manual-task-owner.json"
    owner_path.write_text(json.dumps(record), encoding="utf-8")

    claimed = claim_pending_task_owner_unlocked(
        tmp_path, task, "300", 1, source="recovery",
    )

    assert claimed["owner_run_id"] == "300"
    assert claimed["owner_attempt"] == 1
    assert claimed["rebind_authorization_run_id"] == "200"
    assert claimed["rebind_authorization_run_attempt"] == 1
    assert validate_pending_task_owner_unlocked(tmp_path, task, "300", 1) == claimed


def test_schema_v2_owner_rejects_missing_reconciliation_audit(tmp_path: Path):
    task = "The durable task text remains unchanged."
    now = time.time()
    record = {
        "schema_version": 2,
        "task_sha256": task_digest(task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 1,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 2,
        "source": "recovery",
        "previous_task_sha256": "not-a-sha256",
        "rebind_reason": "owner_authorized_task_content_reconciliation",
        "rebind_authorization_run_id": "200",
        "rebound_at": now,
    }
    owner_path = tmp_path / "pending-manual-task-owner.json"
    owner_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(RuntimeError, match="schema v2"):
        validate_pending_task_owner_unlocked(tmp_path, task, "200", 1)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell task-owner interoperability is Windows-only")
@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.update(previous_task_sha256=record["task_sha256"]),
        lambda record: record.update(source="manual_dispatch"),
        lambda record: record.update(rebind_authorization_run_id="invalid"),
        lambda record: record.update(rebind_authorization_run_attempt=0),
        lambda record: record.update(claim_count=2_147_483_648),
    ],
)
def test_powershell_validator_rejects_inconsistent_schema_v2_audit(
    tmp_path: Path, mutation,
):
    root = Path(__file__).resolve().parents[1]
    module = root / "scripts" / "AutonomousEngineeringTaskState.psm1"
    task = "Schema-v2 validator must reject incomplete audit records."
    now = time.time()
    record = {
        "schema_version": 2,
        "task_sha256": task_digest(task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 1,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 2,
        "source": "recovery",
        "previous_task_sha256": "a" * 64,
        "rebind_reason": "owner_authorized_task_content_reconciliation",
        "rebind_authorization_run_id": "200",
        "rebind_authorization_run_attempt": 1,
        "rebound_at": now,
    }
    mutation(record)
    owner_path = tmp_path / "pending-manual-task-owner.json"
    owner_path.write_text(json.dumps(record), encoding="utf-8")
    script = (
        "$ErrorActionPreference='Stop'; "
        f"Import-Module {_ps_quote(str(module))} -Force; "
        f"Get-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} -Task {_ps_quote(task)} | Out-Null"
    )
    result = _powershell(script)
    assert result.returncode != 0
    assert owner_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell task-owner interoperability is Windows-only")
def test_powershell_owner_rebind_archives_old_record_and_preserves_submission(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    module = root / "scripts" / "AutonomousEngineeringTaskOwnerRebind.psm1"
    owner_path = tmp_path / "pending-manual-task-owner.json"
    old_task = "Old task identity"
    current_task = "Current durable task text remains untouched"
    now = time.time()
    old_record = {
        "schema_version": 1,
        "task_sha256": task_digest(old_task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 3,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 4,
        "source": "legacy_migration",
    }
    old_bytes = json.dumps(old_record, separators=(",", ":")).encode("utf-8")
    owner_path.write_bytes(old_bytes)
    script = (
        "$ErrorActionPreference='Stop'; "
        f"Import-Module {_ps_quote(str(root / 'scripts' / 'AutonomousEngineeringTaskState.psm1'))} -Force; "
        f"Import-Module {_ps_quote(str(module))} -Force; "
        "$run=[pscustomobject]@{id='200';run_attempt=3;path='.github/workflows/autonomous-codex-engineering.yml';"
        "head_branch='main';status='completed';conclusion='failure'}; "
        f"$result=Rebind-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} "
        f"-Task {_ps_quote(current_task)} -RunId '300' -RunAttempt 2 -VerifiedOwnerRun $run; "
        f"$validated=Get-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} -Task {_ps_quote(current_task)}; "
        f"$null=Write-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} -Task {_ps_quote(current_task)} -RunId '400' -RunAttempt 1 -Source 'recovery' -Existing $validated; "
        f"$validatedAgain=Get-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} -Task {_ps_quote(current_task)}; "
        "$validatedAgain | ConvertTo-Json -Compress -Depth 5"
    )
    result = _powershell(script)
    assert result.returncode == 0, result.stderr

    updated = json.loads(owner_path.read_text(encoding="utf-8"))
    backups = list(tmp_path.glob("pending-manual-task-owner.json.quarantine-rebound-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_bytes
    assert updated["schema_version"] == 2
    assert updated["task_sha256"] == task_digest(current_task)
    assert updated["previous_task_sha256"] == old_record["task_sha256"]
    assert updated["rebind_reason"] == "owner_authorized_task_content_reconciliation"
    assert updated["rebind_authorization_run_id"] == "300"
    assert updated["rebind_authorization_run_attempt"] == 2
    assert updated["submitted_run_id"] == "100"
    assert updated["submitted_attempt"] == 1
    assert updated["created_at"] == old_record["created_at"]
    assert updated["owner_run_id"] == "400"
    assert updated["owner_attempt"] == 1
    assert updated["claim_count"] == 6
    assert updated["rebind_authorization_run_id"] == "300"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell task-owner interoperability is Windows-only")
@pytest.mark.parametrize(
    ("run_id", "run_attempt", "path", "branch", "status", "conclusion"),
    [
        ("200", 1, WORKFLOW_PATH, "main", "in_progress", "failure"),
        ("200", 1, WORKFLOW_PATH, "main", "completed", "success"),
        ("200", 1, WORKFLOW_PATH, "main", "completed", "neutral"),
        ("201", 1, WORKFLOW_PATH, "main", "completed", "failure"),
        ("200", 2, WORKFLOW_PATH, "main", "completed", "failure"),
        ("200", 1, ".github/workflows/untrusted.yml", "main", "completed", "failure"),
        ("200", 1, WORKFLOW_PATH, "feature/branch", "completed", "failure"),
    ],
)
def test_powershell_owner_rebind_rejects_unverified_or_successful_owner_run(
    tmp_path: Path, run_id: str, run_attempt: int, path: str, branch: str, status: str, conclusion: str,
):
    root = Path(__file__).resolve().parents[1]
    module = root / "scripts" / "AutonomousEngineeringTaskOwnerRebind.psm1"
    owner_path = tmp_path / f"owner-{run_id}-{status}-{conclusion}.json"
    old_task = "Old task identity"
    current_task = "Current task identity"
    now = time.time()
    record = {
        "schema_version": 1,
        "task_sha256": task_digest(old_task),
        "submitted_run_id": "100",
        "submitted_attempt": 1,
        "owner_run_id": "200",
        "owner_attempt": 1,
        "created_at": now,
        "claimed_at": now,
        "claim_count": 1,
        "source": "manual_dispatch",
    }
    owner_path.write_text(json.dumps(record), encoding="utf-8")
    before = owner_path.read_bytes()
    script = (
        "$ErrorActionPreference='Stop'; "
        f"Import-Module {_ps_quote(str(module))} -Force; "
        f"$run=[pscustomobject]@{{id='{run_id}';run_attempt={run_attempt};path='{path}';head_branch='{branch}';"
        f"status='{status}';conclusion='{conclusion}'}}; "
        f"try {{ Rebind-AutonomousTaskOwnerRecord -Path {_ps_quote(str(owner_path))} "
        f"-Task {_ps_quote(current_task)} -RunId '300' -RunAttempt 1 -VerifiedOwnerRun $run | Out-Null; "
        "exit 9 } catch { exit 0 }"
    )
    result = _powershell(script)
    assert result.returncode == 0, result.stderr
    assert owner_path.read_bytes() == before
    assert not list(tmp_path.glob("*.quarantine-rebound-*.json"))


def test_workflow_exposes_owner_rebind_as_separate_explicit_operation():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    script = (root / "scripts" / "persist_autonomous_manual_task.ps1").read_text(encoding="utf-8")
    run_state = (root / "scripts" / "AutonomousEngineeringRunState.psm1").read_text(encoding="utf-8")

    assert "repair_pending_task_owner:" in workflow
    assert "REPAIR_PENDING_TASK_OWNER:" in workflow
    assert "TASK_OPERATION_REQUESTED:" in workflow
    assert 'TASK_OPERATION_REQUESTED" == "true"' in workflow
    assert "inputs.manual_task != '' || inputs.repair_pending_task_owner" in workflow
    assert "Get-AutonomousWorkflowRun" in script
    assert "-RunAttempt $recordedOwnerAttempt" in script
    assert "Assert-AutonomousWorkflowRunRebindable" in script
    assert "ExpectedRunAttempt $recordedOwnerAttempt" in script
    assert "Rebind-AutonomousTaskOwnerRecord" in script
    assert "requires no pending PR record and no open autonomous PR" in script
    assert "replace_pending_task" in script
    assert "conclusion" in run_state and "completed" in run_state
    assert "timed_out" in run_state
