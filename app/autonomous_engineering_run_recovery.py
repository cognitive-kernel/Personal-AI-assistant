"""Conservative reconciliation of stale Autonomous Engineering progress runs.

The module classifies evidence only; callers must hold the established repository,
durable-state, and checkpoint-store locks before persisting a recovery transition.
"""
from __future__ import annotations

import math
import socket
from dataclasses import asdict, dataclass
from typing import Any, Literal

import psutil

ProcessState = Literal["alive", "missing", "reused", "unknown"]
RecoveryState = Literal["active", "stale", "unknown", "terminal"]

_ACTIVE_REMOTE_STATES = frozenset({
    "queued", "requested", "waiting", "pending", "in_progress", "suspended",
})
_TERMINAL_REMOTE_STATES = frozenset({"completed"})


@dataclass(frozen=True)
class RunRecoveryDecision:
    """A read-only recovery decision bound to one exact run and attempt."""

    state: RecoveryState
    reason: str
    run_id: str | None
    run_attempt: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_lock_owner_process(
    owner: Any,
    *,
    hostname: str | None = None,
    process_factory: Any = None,
) -> ProcessState:
    """Compare PID and OS creation time; never infer liveness from PID alone."""
    if not isinstance(owner, dict):
        return "unknown"
    pid = owner.get("pid")
    started_at = owner.get("process_started_at")
    owner_host = owner.get("host")
    if (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
        or isinstance(started_at, bool) or not isinstance(started_at, (int, float))
        or not math.isfinite(float(started_at))
        or not isinstance(owner_host, str) or not owner_host
    ):
        return "unknown"
    if owner_host.casefold() != (hostname or socket.gethostname()).casefold():
        return "unknown"

    factory = process_factory or psutil.Process
    try:
        process = factory(pid)
        actual_started_at = float(process.create_time())
        if not math.isfinite(actual_started_at):
            return "unknown"
        if not math.isclose(actual_started_at, float(started_at), rel_tol=0.0, abs_tol=0.02):
            return "reused"
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                return "missing"
        except (psutil.AccessDenied, psutil.Error, OSError):
            return "unknown"
        return "alive" if process.is_running() else "missing"
    except (psutil.NoSuchProcess, ProcessLookupError):
        return "missing"
    except (psutil.AccessDenied, psutil.Error, OSError, TypeError, ValueError):
        return "unknown"


def classify_run_recovery(
    current: Any,
    lock_owner: Any,
    *,
    repository_lock_held: bool,
    checkpoint_lock_held: bool,
    process_state: ProcessState,
    remote_status: str | None,
    local_run: bool = False,
) -> RunRecoveryDecision:
    """Classify one progress record without changing or deleting durable state."""
    if not isinstance(current, dict):
        return RunRecoveryDecision("unknown", "current_progress_unreadable", None, None)

    raw_run_id = current.get("run_id")
    raw_attempt = current.get("run_attempt", "1")
    if raw_run_id is None or isinstance(raw_run_id, bool) or not str(raw_run_id).strip():
        return RunRecoveryDecision("unknown", "run_id_missing", None, None)
    if raw_attempt is None or isinstance(raw_attempt, bool) or not str(raw_attempt).strip():
        return RunRecoveryDecision("unknown", "run_attempt_missing", str(raw_run_id), None)
    run_id, attempt = str(raw_run_id), str(raw_attempt)
    status = str(current.get("status") or "").strip().lower()
    if status in {"completed", "succeeded", "success", "complete", "failed", "cancelled", "skipped", "blocked", "interrupted"}:
        return RunRecoveryDecision("terminal", "progress_already_terminal", run_id, attempt)

    if not isinstance(lock_owner, dict):
        return RunRecoveryDecision("unknown", "lock_owner_metadata_missing", run_id, attempt)
    owner_run_id = str(lock_owner.get("run_id") or "")
    owner_attempt = str(lock_owner.get("run_attempt") or "")
    owner_host = str(lock_owner.get("host") or "")
    if owner_run_id != run_id or owner_attempt != attempt:
        return RunRecoveryDecision("unknown", "lock_owner_run_mismatch", run_id, attempt)
    if owner_host.casefold() != socket.gethostname().casefold():
        return RunRecoveryDecision("unknown", "lock_owner_host_mismatch", run_id, attempt)

    if repository_lock_held:
        return RunRecoveryDecision("active", "repository_execution_lock_held", run_id, attempt)
    if checkpoint_lock_held:
        return RunRecoveryDecision("active", "checkpoint_watcher_lock_held", run_id, attempt)
    if process_state == "alive":
        return RunRecoveryDecision("active", "exact_owner_process_alive", run_id, attempt)
    if process_state == "unknown":
        return RunRecoveryDecision("unknown", "owner_process_identity_unverifiable", run_id, attempt)

    remote = str(remote_status or "").strip().lower()
    if remote in _ACTIVE_REMOTE_STATES:
        return RunRecoveryDecision("active", "remote_workflow_still_active", run_id, attempt)
    if remote not in _TERMINAL_REMOTE_STATES and not (local_run and remote_status is None):
        return RunRecoveryDecision("unknown", "remote_workflow_state_unverifiable", run_id, attempt)
    if process_state not in {"missing", "reused"}:
        return RunRecoveryDecision("unknown", "owner_process_state_unverifiable", run_id, attempt)

    return RunRecoveryDecision("stale", "locks_released_owner_process_gone_and_run_terminal", run_id, attempt)
