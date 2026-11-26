"""Prepare a non-destructive native state snapshot; never switch live authority."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

from .autonomous_engineering_state import exclusive_file_lock
from .autonomous_engineering_native_config import validate_native_state_root
from .autonomous_engineering_state_store import RestrictedStateBroker
from .autonomous_engineering_task_owner import validate_owner_record

_LOCKS = ("repository-execution.lock", "autonomous-durable-state.lock", "autonomous-checkpoint-store.lock")
_MARKER = ".native-migration.json"
_MAX_FILES = 10_000
_MAX_BYTES = 512 * 1024 * 1024


def _safe_info(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise RuntimeError("Migration refuses symbolic links and reparse points.")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise RuntimeError("Migration refuses non-regular entries.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise RuntimeError("Migration refuses linked files.")
    return info


def _inventory(root: Path) -> dict[str, bytes]:
    """Bounded exact snapshot. Lock state itself is never transferred."""
    values: dict[str, bytes] = {}
    total = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        for item in sorted(directory.iterdir()):
            info = _safe_info(item)
            relative = item.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                stack.append(item)
                continue
            if relative in _LOCKS or relative == _MARKER:
                continue
            total += info.st_size
            if total > _MAX_BYTES or len(values) >= _MAX_FILES:
                raise RuntimeError("Migration exceeds the bounded snapshot limit.")
            with item.open("rb") as stream:
                value = stream.read(info.st_size + 1)
            if len(value) != info.st_size:
                raise RuntimeError("State changed during migration inventory.")
            if item.suffix == ".json":
                json.loads(value)
            elif item.suffix == ".jsonl":
                for line in value.splitlines():
                    if line.strip():
                        json.loads(line)
            values[relative] = value
    return values


def _validate_pending(root: Path, values: dict[str, bytes]) -> None:
    task = values.get("pending-manual-task.txt")
    owner = values.get("pending-manual-task-owner.json")
    if (task is None) != (owner is None):
        raise RuntimeError("Pending task and owner must be migrated together.")
    if task is not None:
        validate_owner_record(json.loads(owner), task.decode("utf-8"))
    checkpoint = RestrictedStateBroker(root)._checkpoint_summary()
    if checkpoint.get("verified") is False:
        raise RuntimeError("Checkpoint cannot be verified for migration.")


def _manifest(values: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(value).hexdigest() for name, value in sorted(values.items())}


def stage_native_migration(source: Path, target: Path, *, require_idle: Callable[[], None]) -> dict:
    source, target = Path(source), Path(target)
    if not source.is_absolute() or not target.is_absolute():
        raise ValueError("Migration roots must be absolute.")
    if not stat.S_ISDIR(_safe_info(source).st_mode):
        raise RuntimeError("Legacy state root is not a directory.")
    src, dst = source.resolve(), target.resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise RuntimeError("Migration roots must be disjoint.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    validate_native_state_root(target.parent)
    # One explicit operation at a time; acquire source locks in the global
    # order. A private staging directory is not a live state owner.
    with exclusive_file_lock(target.parent / ".native-migration.lock", timeout_seconds=0,
                             timeout_message="Another migration is active."):
        with ExitStack() as stack:
            for name in _LOCKS:
                stack.enter_context(exclusive_file_lock(source / name, timeout_seconds=0,
                    timeout_message="Legacy state is owned by another process."))
            require_idle()
            values = _inventory(source)
            _validate_pending(source, values)
            # Preserve stale repository owner evidence without copying a held
            # byte-zero lock. Reading begins after the reserved lock byte.
            with (source / _LOCKS[0]).open("rb") as stream:
                stream.seek(1)
                owner = stream.read(4097)
            if len(owner) > 4096:
                raise RuntimeError("Repository owner evidence exceeds its limit.")
            # Earlier shared locks wrote a two-byte literal backslash-zero placeholder.
            # A staged snapshot is not live authority; do not treat it as owner metadata.
            if owner == b"0":
                owner = b""
            if owner and not isinstance(json.loads(owner), dict):
                raise RuntimeError("Repository owner evidence must be a JSON object.")
            manifest = _manifest(values)
            marker = {
                "schema_version": 1, "state": "staged", "activation_permitted": False,
                "source_root_sha256": hashlib.sha256(str(src).encode()).hexdigest(),
                "files": manifest, "repository_owner_sha256": hashlib.sha256(owner).hexdigest(),
            }
            if target.exists() or target.is_symlink():
                validate_native_state_root(target)
                marker_path = target / _MARKER
                _safe_info(marker_path)
                if json.loads(marker_path.read_bytes()) != marker or _manifest(_inventory(target)) != manifest:
                    raise RuntimeError("Existing native state conflicts with the verified snapshot.")
                if owner:
                    with (target / _LOCKS[0]).open("rb") as stream:
                        stream.seek(1)
                        if stream.read(4097) != owner:
                            raise RuntimeError("Existing native owner evidence conflicts.")
            else:
                stage = Path(tempfile.mkdtemp(prefix=".native-migration-stage-", dir=target.parent))
                try:
                    # Private destination, no user-provided archive paths.
                    for relative, value in values.items():
                        path = stage.joinpath(*relative.split("/"))
                        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(value)
                            stream.flush()
                            os.fsync(stream.fileno())
                    if owner:
                        (stage / _LOCKS[0]).write_bytes(b"0" + owner)
                    (stage / _MARKER).write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
                    if _manifest(_inventory(stage)) != manifest or _manifest(_inventory(source)) != manifest:
                        raise RuntimeError("Migration hash verification failed.")
                    require_idle()
                    if target.exists() or target.is_symlink():
                        raise RuntimeError("Destination appeared during migration.")
                    os.rename(stage, target)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
    return {
        "staged": True, "activated": False, "files_verified": len(manifest),
        "message": "نسخهٔ مهاجرت با بررسی hash آماده شد. مسیر فعال و سرویس قدیمی تغییر نکرده‌اند.",
    }
