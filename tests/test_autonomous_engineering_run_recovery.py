from __future__ import annotations

import socket

import psutil

from app.autonomous_engineering_run_recovery import (
    classify_run_recovery,
    inspect_lock_owner_process,
)


def _owner(run_id="run-42", attempt="2", *, pid=1234, started_at=100.0):
    return {
        "schema_version": 2,
        "pid": pid,
        "process_started_at": started_at,
        "owner_token": "a" * 64,
        "run_id": run_id,
        "run_attempt": attempt,
        "host": socket.gethostname(),
    }


def _current(run_id="run-42", attempt="2", status="running"):
    return {
        "run_id": run_id, "run_attempt": attempt, "status": status,
        "execution_owner_token": "a" * 64,
    }


def test_stopped_nonterminal_run_is_recoverable_when_remote_run_is_terminal():
    decision = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="missing",
        remote_status="completed",
    )

    assert decision.state == "stale"
    assert decision.run_id == "run-42"
    assert decision.run_attempt == "2"


def test_really_active_repository_owner_is_protected_from_cleanup():
    decision = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=True,
        checkpoint_lock_held=False,
        process_state="alive",
        remote_status="in_progress",
    )

    assert decision.state == "active"
    assert decision.reason == "repository_execution_lock_held"


def test_active_checkpoint_watcher_is_protected_independently():
    decision = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=True,
        process_state="missing",
        remote_status="completed",
    )

    assert decision.state == "active"
    assert decision.reason == "checkpoint_watcher_lock_held"


def test_stale_nonterminal_progress_is_not_recovered_while_remote_workflow_is_active():
    decision = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="missing",
        remote_status="queued",
    )

    assert decision.state == "active"
    assert decision.reason == "remote_workflow_still_active"


def test_unknown_process_or_remote_evidence_fails_closed():
    process_unknown = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="unknown",
        remote_status="completed",
    )
    remote_unknown = classify_run_recovery(
        _current(),
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="missing",
        remote_status=None,
    )

    assert process_unknown.state == "unknown"
    assert remote_unknown.state == "unknown"


def test_lock_owner_from_another_run_cannot_reconcile_current_progress():
    decision = classify_run_recovery(
        _current(run_id="run-new"),
        _owner(run_id="run-old"),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="missing",
        remote_status="completed",
    )

    assert decision.state == "unknown"
    assert decision.reason == "lock_owner_run_mismatch"



def test_owner_token_mismatch_cannot_reconcile_a_run():
    current = _current()
    current["execution_owner_token"] = "b" * 64
    decision = classify_run_recovery(
        current,
        _owner(),
        repository_lock_held=False,
        checkpoint_lock_held=False,
        process_state="missing",
        remote_status="completed",
    )

    assert decision.state == "unknown"
    assert decision.reason == "lock_owner_token_mismatch"

def test_pid_reuse_is_detected_by_process_creation_time():
    class FakeProcess:
        def create_time(self):
            return 200.0

        def status(self):
            return psutil.STATUS_RUNNING

        def is_running(self):
            return True

    assert inspect_lock_owner_process(
        _owner(started_at=100.0),
        hostname=socket.gethostname(),
        process_factory=lambda _pid: FakeProcess(),
    ) == "reused"


def test_matching_pid_and_creation_time_requires_live_process_confirmation():
    class FakeProcess:
        def create_time(self):
            return 100.0

        def status(self):
            return psutil.STATUS_RUNNING

        def is_running(self):
            return True

    assert inspect_lock_owner_process(
        _owner(started_at=100.0),
        hostname=socket.gethostname(),
        process_factory=lambda _pid: FakeProcess(),
    ) == "alive"


def test_owner_on_another_host_is_unknown_not_stale():
    owner = _owner()
    owner["host"] = "different-host"

    assert inspect_lock_owner_process(
        owner,
        hostname=socket.gethostname(),
        process_factory=lambda _pid: None,
    ) == "unknown"



def test_legacy_or_invalid_fencing_metadata_cannot_authorize_recovery():
    for token in (None, "x" * 64, "a" * 63):
        current, owner = _current(), _owner()
        if token is None:
            current.pop("execution_owner_token")
            owner.pop("owner_token")
        else:
            current["execution_owner_token"] = owner["owner_token"] = token
        decision = classify_run_recovery(
            current, owner, repository_lock_held=False, checkpoint_lock_held=False,
            process_state="missing", remote_status="completed",
        )
        assert decision.state == "unknown"


def test_missing_attempt_does_not_default_to_first_attempt():
    current = _current()
    current.pop("run_attempt")
    decision = classify_run_recovery(
        current, _owner(attempt="1"), repository_lock_held=False, checkpoint_lock_held=False,
        process_state="missing", remote_status="completed",
    )
    assert decision.state == "unknown"
