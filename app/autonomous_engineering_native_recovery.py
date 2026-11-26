"""Explicit native cleanup with exact-attempt stale progress reconciliation.

No automatic recovery on GET/startup; never rewrites checkpoint or task files.
Recovery event is committed before the projection, and a retry is idempotent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .autonomous_engineering_native_config import validate_native_state_root
from .autonomous_engineering_run_recovery import (
    RunRecoveryDecision, classify_run_recovery, inspect_lock_owner_process,
)
from .autonomous_engineering_state_broker import StateBrokerConflict
from .autonomous_engineering_state_store import RestrictedStateBroker


def _read_regular(path: Path, limit: int, *, skip: int = 0) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or info.st_size > limit + skip
    ):
        raise OSError("Recovery input is not a bounded regular file.")
    with path.open("rb") as stream:
        stream.seek(skip)
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise OSError("Recovery input exceeds its size limit.")
    return value


def _atomic_write(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".reconcile-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _encode(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def reconcile_progress_locked(
    root: Path, verify_remote: Callable[[int], dict[str, Any]],
) -> RunRecoveryDecision:
    """Caller holds all three state locks; verify exact current remote attempt."""
    try:
        raw = _read_regular(root / "current.json", 256_000)
        if raw is None:
            return RunRecoveryDecision("terminal", "no_current_progress", None, None)
        current = json.loads(raw)
        if not isinstance(current, dict):
            return RunRecoveryDecision("unknown", "current_progress_unreadable", None, None)
        run_id, attempt = str(current.get("run_id", "")), str(current.get("run_attempt", ""))
        terminal = classify_run_recovery(
            current, None, repository_lock_held=False, checkpoint_lock_held=False,
            process_state="unknown", remote_status=None,
        )
        if terminal.state == "terminal":
            return terminal
        if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id) or not re.fullmatch(r"[1-9][0-9]{0,9}", attempt):
            return RunRecoveryDecision("unknown", "remote_run_identity_unverifiable", None, None)
        raw_owner = _read_regular(root / "repository-execution.lock", 4096, skip=1)
        owner = json.loads(raw_owner) if raw_owner else None
        remote = verify_remote(int(run_id))
        if (
            type(remote.get("id")) is not int or remote["id"] != int(run_id)
            or type(remote.get("run_attempt")) is not int or remote["run_attempt"] != int(attempt)
        ):
            return RunRecoveryDecision("unknown", "remote_run_attempt_mismatch", run_id, attempt)
        decision = classify_run_recovery(
            current, owner, repository_lock_held=False, checkpoint_lock_held=False,
            process_state=inspect_lock_owner_process(owner), remote_status=remote.get("status"),
        )
        if decision.state != "stale":
            return decision
        # Keep the original projection in an immutable private recovery record.
        digest = hashlib.sha256(raw).hexdigest()
        archive = root / f"interrupted-progress-{digest}.json"
        prior = _read_regular(archive, 256_000)
        if prior is None:
            _atomic_write(archive, raw)
        elif prior != raw:
            return RunRecoveryDecision("unknown", "recovery_archive_conflict", run_id, attempt)
        events_path = root / "events.jsonl"
        events = _read_regular(events_path, 4 * 1024 * 1024) or b""
        lines = events.splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        existing = [e for e in records if isinstance(e, dict) and e.get("recovery_id") == digest]
        if len(existing) > 1:
            return RunRecoveryDecision("unknown", "recovery_event_conflict", run_id, attempt)
        updated_at = datetime.now(timezone.utc).isoformat()
        if existing:
            event = existing[0]
            if event.get("status") != "interrupted" or event.get("run_id") != run_id or event.get("run_attempt") != attempt:
                return RunRecoveryDecision("unknown", "recovery_event_conflict", run_id, attempt)
            updated_at = event["updated_at"]
        else:
            event = {
                "type": "progress_event", "status": "interrupted", "run_id": run_id,
                "run_attempt": attempt, "updated_at": updated_at, "phase_label": "بازیابی وضعیت",
                "activity": "اجرای متوقف‌شده با شواهد معتبر تعیین تکلیف شد.",
                "recovery_id": digest, "recovery_reason": decision.reason,
            }
            _atomic_write(events_path, events + (b"\n" if events and not events.endswith(b"\n") else b"") + _encode(event))
        updated = dict(current)
        updated.update(status="interrupted", updated_at=updated_at, recovery_id=digest,
                       recovery_reason=decision.reason, phase_label="بازیابی وضعیت",
                       activity="اجرای متوقف‌شده؛ اطلاعات قبلی حفظ شده‌اند.")
        _atomic_write(root / "current.json", _encode(updated))
        return decision
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return RunRecoveryDecision("unknown", "recovery_evidence_or_storage_unavailable", None, None)


def cleanup_native_progress(root: Path, *, verify_remote, ensure_remote_idle) -> dict[str, Any]:
    validate_native_state_root(root)
    store = RestrictedStateBroker(root)

    def reconcile(locked_root: Path) -> RunRecoveryDecision:
        # Repeat authoritative admission under the local locks.
        ensure_remote_idle()
        return reconcile_progress_locked(locked_root, verify_remote)

    result = store.cleanup(reconcile=reconcile)
    if result.get("conflict"):
        raise StateBrokerConflict("Native recovery/cleanup refused.", result.get("blockers", []))
    return result
