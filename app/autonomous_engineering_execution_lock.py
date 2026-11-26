from __future__ import annotations

import json
import math
import os
import secrets
import socket
import time

import psutil
from contextlib import contextmanager
from typing import Iterator

from .autonomous_engineering_state import execution_state_directory

_OWNER_TOKEN_ENV = "AUTONOMOUS_ENGINEERING_EXECUTION_OWNER_TOKEN"


def _open_repository_lock_file(lock_path, *, create: bool):
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    descriptor = os.open(lock_path, flags, 0o600)
    return os.fdopen(descriptor, "r+b")


@contextmanager
def repository_execution_lock(run_id: str, run_attempt: str, *, record_owner: bool = True) -> Iterator[None]:
    """Own the repository worktree exclusively across processes and sessions."""
    state_dir = execution_state_directory()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "repository-execution.lock"
    handle = _open_repository_lock_file(lock_path, create=True)
    acquired = False
    previous_owner_token = os.environ.get(_OWNER_TOKEN_ENV)
    owner_token = secrets.token_hex(32)
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            # Byte zero is reserved for the cross-platform lock on every OS.
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(
                    "Another autonomous execution owns this repository worktree. "
                    "This run exited without touching its checkpoint or working tree."
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "Another autonomous execution owns this repository worktree. "
                    "This run exited without touching its checkpoint or working tree."
                ) from exc
        acquired = True
        if not record_owner:
            # Maintenance must not destroy the prior run's fencing evidence.
            yield
            return
        try:
            process_started_at = float(psutil.Process(os.getpid()).create_time())
        except (psutil.Error, OSError, ValueError) as exc:
            raise RuntimeError("The repository execution owner process identity cannot be verified.") from exc
        owner_metadata = json.dumps(
            {
                "schema_version": 2,
                "pid": os.getpid(),
                "process_started_at": process_started_at,
                "owner_token": owner_token,
                "run_id": str(run_id),
                "run_attempt": str(run_attempt),
                "host": socket.gethostname(),
                "acquired_at": time.time(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        # Byte zero is the cross-platform lock. Keep readable owner metadata after it:
        # Windows byte-range locks reject reads that overlap the locked byte.
        handle.seek(1)
        handle.write(owner_metadata)
        handle.truncate(1 + len(owner_metadata))
        handle.flush()
        os.environ[_OWNER_TOKEN_ENV] = owner_token
        try:
            yield
        finally:
            if previous_owner_token is None:
                os.environ.pop(_OWNER_TOKEN_ENV, None)
            else:
                os.environ[_OWNER_TOKEN_ENV] = previous_owner_token
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

def require_parent_repository_execution_lock(run_id: str) -> None:
    """Require this subprocess to be launched by the cycle process holding the shared lock."""
    lock_path = execution_state_directory() / "repository-execution.lock"
    try:
        handle = _open_repository_lock_file(lock_path, create=True)
    except OSError as exc:
        raise RuntimeError("The repository execution lock owner cannot be verified.") from exc

    held_by_another_process = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                held_by_another_process = True
            else:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                held_by_another_process = True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        if not held_by_another_process:
            raise RuntimeError("PR publication must run as a child of the locked cycle controller.")

        handle.seek(1)
        try:
            owner = json.loads(handle.read(4096).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("The repository execution lock owner metadata is invalid.") from exc
        try:
            parent_started_at = float(psutil.Process(os.getppid()).create_time())
            recorded_started_at = float(owner.get("process_started_at")) if isinstance(owner, dict) else float("nan")
        except (psutil.Error, OSError, TypeError, ValueError) as exc:
            raise RuntimeError("The repository execution lock owner process identity cannot be verified.") from exc
        if (
            not isinstance(owner, dict)
            or owner.get("pid") != os.getppid()
            or owner.get("run_id") != str(run_id)
            or owner.get("host") != socket.gethostname()
            or not math.isfinite(recorded_started_at)
            or not math.isclose(recorded_started_at, parent_started_at, rel_tol=0.0, abs_tol=0.02)
            or not isinstance(owner.get("owner_token"), str)
            or os.environ.get(_OWNER_TOKEN_ENV) != owner.get("owner_token")
        ):
            raise RuntimeError("PR publication is not owned by the active cycle controller.")
    finally:
        handle.close()
