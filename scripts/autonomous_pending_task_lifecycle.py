"""Fail-closed lifecycle storage for the single durable manual-task slot."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
        raise LifecycleConflict("Pending task owner metadata does not match the task.", ["owner_metadata_mismatch"]) from exc


def _revision(task: bytes, owner: bytes, blockers: list[str]) -> str:
    return _digest((_digest(task) + "|" + _digest(owner) + "|" + ",".join(sorted(blockers))).encode("utf-8"))


def _archive_revision(task: bytes, owner: bytes, manifest: dict[str, Any]) -> str:
    return _digest((
        _digest(task) + "|" + _digest(owner) + "|" +
        str(manifest.get("archived_at_ms", "")) + "|" +
        str(manifest.get("restored_at_ms", ""))
    ).encode("utf-8"))


def _state_blockers(state: Path) -> list[str]:
    blockers: list[str] = []
    if _regular(state / "pending-autonomous-pr.json", 64 * 1024) is not None:
        blockers.append("pending_pr")
    if _regular(state / "pending-state-cleanup.json", 64 * 1024) is not None:
        blockers.append("cleanup_in_progress")
    current_raw = _regular(state / "current.json", 256_000)
    if current_raw is not None:
        current = _json(current_raw, "current_progress_unreadable")
        status = current.get("status")
        terminal = {"completed", "succeeded", "success", "complete", "failed", "cancelled", "skipped", "blocked"}
        if not isinstance(status, str) or status.lower() not in terminal:
            blockers.append("active_or_unknown_run")
    if _regular(state / "latest-worktree-checkpoint.json", 16 * 1024) is not None:
        blockers.append("checkpoint_present_or_unknown")
    if any(state.glob("worktree-checkpoint-*")) or any(state.glob("worktree-plan-*")):
        blockers.append("checkpoint_artifacts_present")
    return blockers


def _probe_lock(state: Path, filename: str) -> bool:
    import fcntl
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(state / filename, flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


def _mutation_blockers(state: Path) -> list[str]:
    blockers = _state_blockers(state)
    if not _probe_lock(state, "repository-execution.lock"):
        blockers.append("repository_execution_active")
    if not _probe_lock(state, "autonomous-checkpoint-store.lock"):
        blockers.append("checkpoint_writer_active")
    return sorted(set(blockers))


def _archive_root(state: Path) -> Path:
    root = state / ARCHIVE_DIR
    try:
        info = root.lstat()
    except FileNotFoundError:
        root.mkdir(mode=0o700)
        info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077:
        raise LifecycleConflict("Archive storage is not a protected directory.", ["archive_storage_invalid"])
    return root


def _atomic_write(path: Path, raw: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
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
    blockers = _state_blockers(state)
    return [{
        "task_id": _digest(task + b"\0" + owner_raw),
        "revision": _revision(task, owner_raw, blockers),
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
        owner = _safe_owner(task, owner_raw)
        manifest = _json(manifest_raw, "archive_manifest_invalid")
        if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("task_id") != entry.name:
            raise LifecycleConflict("Archived task manifest is inconsistent.", ["archive_manifest_mismatch"])
        if manifest.get("task_sha256") != _digest(task) or manifest.get("owner_sha256") != _digest(owner_raw):
            raise LifecycleConflict("Archived task integrity check failed.", ["archive_integrity_mismatch"])
        result.append({
            "task_id": entry.name,
            "revision": _archive_revision(task, owner_raw, manifest),
            "status": "restored" if manifest.get("restored_at_ms") is not None else "archived",
            "created_at": owner.get("created_at"),
            "archived_at": manifest.get("archived_at_ms"),
            "source": owner.get("source"),
            "restorable": manifest.get("restored_at_ms") is None,
        })
    return result


def archive_task(state: Path, task_id: str, revision: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", task_id or "") or not re.fullmatch(r"[a-f0-9]{64}", revision or ""):
        raise LifecycleConflict("Invalid task identity or revision.", ["invalid_identity"])
    root = _archive_root(state)
    final = root / task_id
    task_path, owner_path = state / TASK_FILE, state / OWNER_FILE

    if final.exists():
        saved_task = _regular(final / "task.txt", MAX_TASK_BYTES)
        saved_owner = _regular(final / "owner.json", 64 * 1024)
        manifest_raw = _regular(final / "manifest.json", 64 * 1024)
        if saved_task is None or saved_owner is None or manifest_raw is None:
            raise LifecycleConflict("Existing archive entry is incomplete.", ["archive_package_incomplete"])
        manifest = _json(manifest_raw, "archive_manifest_invalid")
        if manifest.get("source_revision") != revision:
            raise LifecycleConflict("Archive retry revision is stale.", ["stale_revision"])
        active_task = _regular(task_path, MAX_TASK_BYTES)
        active_owner = _regular(owner_path, 64 * 1024)
        if active_task not in (None, saved_task) or active_owner not in (None, saved_owner):
            raise LifecycleConflict("Archive recovery found different pending state.", ["archive_collision"])
        if active_task is not None:
            task_path.unlink()
        if active_owner is not None:
            owner_path.unlink()
        return {"task_id": task_id, "status": "archived", "preserved": True}

    pending = list_pending(state)
    if len(pending) != 1 or pending[0]["task_id"] != task_id:
        raise LifecycleConflict("Pending task identity is stale.", ["stale_task"])
    if pending[0]["revision"] != revision:
        raise LifecycleConflict("Pending task revision is stale.", ["stale_revision"])
    blockers = _mutation_blockers(state)
    if blockers:
        raise LifecycleConflict("Task archive was refused by safety guards.", blockers)
    task = _regular(task_path, MAX_TASK_BYTES)
    owner_raw = _regular(owner_path, 64 * 1024)
    if task is None or owner_raw is None:
        raise LifecycleConflict("Pending task changed during archive.", ["task_changed"])

    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(root)))
    try:
        os.chmod(stage, 0o700)
        _atomic_write(stage / "task.txt", task)
        _atomic_write(stage / "owner.json", owner_raw)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "source_revision": revision,
            "archived_at_ms": int(time.time() * 1000),
            "task_sha256": _digest(task),
            "owner_sha256": _digest(owner_raw),
        }
        _atomic_write(stage / "manifest.json", _canonical(manifest) + b"\n")
        os.replace(stage, final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    task_path.unlink()
    owner_path.unlink()
    return {"task_id": task_id, "status": "archived", "preserved": True}


def restore_task(state: Path, task_id: str, revision: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", task_id or "") or not re.fullmatch(r"[a-f0-9]{64}", revision or ""):
        raise LifecycleConflict("Invalid task identity or revision.", ["invalid_identity"])
    archived = next((item for item in list_archived(state) if item["task_id"] == task_id), None)
    if archived is None or archived["status"] != "archived":
        raise LifecycleConflict("Archived task is unavailable.", ["stale_task"])
    if archived["revision"] != revision:
        raise LifecycleConflict("Archived task revision is stale.", ["stale_revision"])

    package = _archive_root(state) / task_id
    task = _regular(package / "task.txt", MAX_TASK_BYTES)
    owner_raw = _regular(package / "owner.json", 64 * 1024)
    manifest_raw = _regular(package / "manifest.json", 64 * 1024)
    if task is None or owner_raw is None or manifest_raw is None:
        raise LifecycleConflict("Archived task package is incomplete.", ["archive_package_incomplete"])
    _safe_owner(task, owner_raw)
    manifest = _json(manifest_raw, "archive_manifest_invalid")

    active_task = _regular(state / TASK_FILE, MAX_TASK_BYTES)
    active_owner = _regular(state / OWNER_FILE, 64 * 1024)
    if active_task not in (None, task) or active_owner not in (None, owner_raw):
        raise LifecycleConflict("The pending task slot is occupied.", ["pending_slot_occupied"])
    blockers = _mutation_blockers(state)
    if blockers:
        raise LifecycleConflict("Task restore was refused by safety guards.", blockers)
    if active_task is None:
        _atomic_write(state / TASK_FILE, task)
    if active_owner is None:
        _atomic_write(state / OWNER_FILE, owner_raw)
    manifest["restored_at_ms"] = int(time.time() * 1000)
    _atomic_write(package / "manifest.json", _canonical(manifest) + b"\n")
    return {"task_id": task_id, "status": "restored", "preserved": True}
