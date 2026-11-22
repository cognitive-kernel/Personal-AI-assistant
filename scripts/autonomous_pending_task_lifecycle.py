"""Fail-closed lifecycle storage for the single durable manual-task slot.

The module is transport-independent so the Windows and POSIX adapters can
implement the same on-disk format. Callers must hold autonomous-durable-state.lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

ARCHIVE_DIR = "archived-pending-tasks"
TASK_FILE = "pending-manual-task.txt"
OWNER_FILE = "pending-manual-task-owner.json"
SCHEMA_VERSION = 1
MAX_TASK_BYTES = 8192
_ACTIVE = {"queued", "pending", "starting", "running", "in_progress"}


class LifecycleConflict(RuntimeError):
    def __init__(self, message: str, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or []


def _regular(path: Path, maximum: int | None = None) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise LifecycleConflict("Task state contains a non-regular entry.", ["state_entry_invalid"])
    if maximum is not None and info.st_size > maximum:
        raise LifecycleConflict("Task state exceeds its size limit.", ["state_entry_oversized"])
    return path.read_bytes()


def _json(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LifecycleConflict("Task lifecycle metadata is unreadable.", [code]) from exc
    if not isinstance(value, dict):
        raise LifecycleConflict("Task lifecycle metadata is invalid.", [code])
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_owner(task: bytes, owner_raw: bytes | None) -> dict[str, Any]:
    if owner_raw is None:
        raise LifecycleConflict("Pending task owner metadata is missing.", ["owner_metadata_missing"])
    owner = _json(owner_raw, "owner_metadata_unreadable")
    try:
        from autonomous_engineering_pending_task_state import _validate_owner_record
        return _validate_owner_record(owner, task.decode("utf-8"))
    except (RuntimeError, UnicodeDecodeError, ImportError) as exc:
        raise LifecycleConflict(
            "Pending task owner metadata does not match the task.",
            ["owner_metadata_mismatch"],
        ) from exc


def _revision(task: bytes, owner_raw: bytes, manifest: dict[str, Any] | None = None) -> str:
    return _digest(_canonical({
        "task_sha256": _digest(task),
        "owner_sha256": _digest(owner_raw),
        "manifest": manifest or {},
    }))


def _read_active_state(state: Path) -> list[str]:
    blockers: list[str] = []
    pr = state / "pending-autonomous-pr.json"
    if _regular(pr, 64 * 1024) is not None:
        blockers.append("pending_pr")
    cleanup = state / "pending-state-cleanup.json"
    if _regular(cleanup, 64 * 1024) is not None:
        blockers.append("cleanup_in_progress")
    current_raw = _regular(state / "current.json", 256_000)
    if current_raw is not None:
        current = _json(current_raw, "current_progress_unreadable")
        run_id = current.get("run_id")
        status = current.get("status")
        if run_id is not None and (
            not isinstance(status, str) or status.lower() in _ACTIVE
        ):
            blockers.append("active_or_unknown_run")
    checkpoint_index = _regular(state / "latest-worktree-checkpoint.json", 16 * 1024)
    if checkpoint_index is not None:
        # This lifecycle store does not relocate checkpoint/plan artifacts.
        # Until a verified task-to-checkpoint package transaction exists, any
        # pointer or artifact blocks mutation, including inactive/legacy state.
        blockers.append("checkpoint_present_or_unknown")
    if any(state.glob("worktree-checkpoint-*")) or any(state.glob("worktree-plan-*")):
        blockers.append("checkpoint_artifacts_present")
    return blockers


def _probe_lock(state: Path, filename: str) -> bool:
    import fcntl
    path = state / filename
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _mutation_blockers(state: Path) -> list[str]:
    blockers = _read_active_state(state)
    if not _probe_lock(state, "repository-execution.lock"):
        blockers.append("repository_execution_active")
    if not _probe_lock(state, "autonomous-checkpoint-store.lock"):
        blockers.append("checkpoint_writer_active")
    return blockers


def _archive_root(state: Path) -> Path:
    root = state / ARCHIVE_DIR
    try:
        info = root.lstat()
    except FileNotFoundError:
        root.mkdir(mode=0o700)
        info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise LifecycleConflict("Archive storage is not a protected directory.", ["archive_storage_invalid"])
    if info.st_mode & 0o077:
        raise LifecycleConflict("Archive storage permissions are too broad.", ["archive_storage_permissions"])
    return root


def _atomic_write(path: Path, raw: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def list_pending(state: Path) -> list[dict[str, Any]]:
    task = _regular(state / TASK_FILE, MAX_TASK_BYTES)
    if task is None:
        return []
    try:
        task.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LifecycleConflict("Pending task is not valid UTF-8.", ["task_encoding_invalid"]) from exc
    owner_raw = _regular(state / OWNER_FILE, 64 * 1024)
    owner = _safe_owner(task, owner_raw)
    assert owner_raw is not None
    task_id = _digest(task + b"\\0" + owner_raw)
    blockers = _read_active_state(state)
    revision = _digest((_digest(task) + "|" + _digest(owner_raw) + "|" + ",".join(sorted(blockers))).encode("utf-8"))
    return [{
        "task_id": task_id,
        "revision": revision,
        "status": "pending",
        "created_at": owner.get("created_at"),
        "owner_run_id": owner.get("owner_run_id"),
        "owner_attempt": owner.get("owner_attempt"),
        "source": owner.get("source"),
        "blocked": bool(blockers),
        "blockers": blockers,
    }]


def list_archived(state: Path) -> list[dict[str, Any]]:
    root = _archive_root(state)
    result: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not re.fullmatch(r"[a-f0-9]{64}", entry.name):
            raise LifecycleConflict("Archive contains an unknown entry.", ["archive_entry_unknown"])
        info = entry.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise LifecycleConflict("Archive contains an unsafe entry.", ["archive_entry_invalid"])
        task = _regular(entry / "task.txt", MAX_TASK_BYTES)
        owner_raw = _regular(entry / "owner.json", 64 * 1024)
        manifest_raw = _regular(entry / "manifest.json", 64 * 1024)
        if task is None or owner_raw is None or manifest_raw is None:
            raise LifecycleConflict("Archived task package is incomplete.", ["archive_package_incomplete"])
        manifest = _json(manifest_raw, "archive_manifest_invalid")
        owner = _safe_owner(task, owner_raw)
        if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("task_id") != entry.name:
            raise LifecycleConflict("Archived task manifest is inconsistent.", ["archive_manifest_mismatch"])
        revision = _digest((
            _digest(task) + "|" + _digest(owner_raw) + "|" +
            str(manifest.get("archived_at_ms", "")) + "|" +
            str(manifest.get("restored_at_ms", ""))
        ).encode("utf-8"))
        result.append({
            "task_id": entry.name,
            "revision": revision,
            "status": "restored" if manifest.get("restored_at") else "archived",
            "created_at": owner.get("created_at"),
            "archived_at": manifest.get("archived_at"),
            "source": owner.get("source"),
            "restorable": not bool(manifest.get("restored_at")),
        })
    return result


def archive_task(state: Path, task_id: str, revision: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", task_id or "") or not re.fullmatch(r"[a-f0-9]{64}", revision or ""):
        raise LifecycleConflict("Invalid task identity or revision.", ["invalid_identity"])
    pending = list_pending(state)
    if len(pending) != 1 or pending[0]["task_id"] != task_id:
        raise LifecycleConflict("Pending task identity is stale.", ["stale_task"])
    if pending[0]["revision"] != revision:
        raise LifecycleConflict("Pending task revision is stale.", ["stale_revision"])
    blockers = _mutation_blockers(state)
    if blockers:
        raise LifecycleConflict("Task archive was refused by safety guards.", blockers)
    task_path, owner_path = state / TASK_FILE, state / OWNER_FILE
    task, owner_raw = _regular(task_path, MAX_TASK_BYTES), _regular(owner_path, 64 * 1024)
    if task is None or owner_raw is None:
        raise LifecycleConflict("Pending task changed during archive.", ["task_changed"])
    root = _archive_root(state)
    final = root / task_id
    if final.exists():
        archived = next((x for x in list_archived(state) if x["task_id"] == task_id), None)
        if archived is None:
            raise LifecycleConflict("Existing archive entry is invalid.", ["archive_collision"])
        if task != (final / "task.txt").read_bytes() or owner_raw != (final / "owner.json").read_bytes():
            raise LifecycleConflict("Archive collision does not match pending state.", ["archive_collision"])
    else:
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(root)))
        try:
            os.chmod(stage, 0o700)
            _atomic_write(stage / "task.txt", task)
            _atomic_write(stage / "owner.json", owner_raw)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "task_id": task_id,
                "archived_at_ms": int(time.time() * 1000),
                "task_sha256": _digest(task),
                "owner_sha256": _digest(owner_raw),
            }
            _atomic_write(stage / "manifest.json", _canonical(manifest) + b"\n")
            os.replace(stage, final)
        finally:
            if stage.exists():
                import shutil
                shutil.rmtree(stage)
    # Archive is durable before the active slot is cleared. If interrupted,
    # a retry verifies byte identity before completing this idempotent cleanup.
    task_path.unlink()
    owner_path.unlink()
    return {"task_id": task_id, "status": "archived", "preserved": True}


def restore_task(state: Path, task_id: str, revision: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", task_id or "") or not re.fullmatch(r"[a-f0-9]{64}", revision or ""):
        raise LifecycleConflict("Invalid task identity or revision.", ["invalid_identity"])
    archived = next((x for x in list_archived(state) if x["task_id"] == task_id), None)
    if archived is None or archived["status"] != "archived":
        raise LifecycleConflict("Archived task is unavailable.", ["stale_task"])
    if archived["revision"] != revision:
        raise LifecycleConflict("Archived task revision is stale.", ["stale_revision"])
    if _regular(state / TASK_FILE, MAX_TASK_BYTES) is not None or _regular(state / OWNER_FILE, 64 * 1024) is not None:
        raise LifecycleConflict("The pending task slot is occupied.", ["pending_slot_occupied"])
    blockers = _mutation_blockers(state)
    if blockers:
        raise LifecycleConflict("Task restore was refused by safety guards.", blockers)
    package = _archive_root(state) / task_id
    task = _regular(package / "task.txt", MAX_TASK_BYTES)
    owner_raw = _regular(package / "owner.json", 64 * 1024)
    manifest_raw = _regular(package / "manifest.json", 64 * 1024)
    if task is None or owner_raw is None or manifest_raw is None:
        raise LifecycleConflict("Archived task package is incomplete.", ["archive_package_incomplete"])
    _safe_owner(task, owner_raw)
    manifest = _json(manifest_raw, "archive_manifest_invalid")
    if manifest.get("task_sha256") != _digest(task) or manifest.get("owner_sha256") != _digest(owner_raw):
        raise LifecycleConflict("Archived task integrity check failed.", ["archive_integrity_mismatch"])
    # Write task first; if owner write fails, the intact archive remains the
    # recovery source and subsequent calls fail closed rather than overwrite it.
    _atomic_write(state / TASK_FILE, task)
    _atomic_write(state / OWNER_FILE, owner_raw)
    manifest["restored_at_ms"] = int(time.time() * 1000)
    _atomic_write(package / "manifest.json", _canonical(manifest) + b"\n")
    return {"task_id": task_id, "status": "restored", "preserved": True}
