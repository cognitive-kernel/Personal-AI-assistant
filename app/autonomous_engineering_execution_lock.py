from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from typing import Iterator

from .autonomous_engineering_state import execution_state_directory


@contextmanager
def repository_execution_lock(run_id: str, run_attempt: str) -> Iterator[None]:
    """Own the repository worktree exclusively across processes and sessions."""
    state_dir = execution_state_directory()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "repository-execution.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
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
        owner_metadata = json.dumps(
            {
                "pid": os.getpid(),
                "run_id": run_id,
                "run_attempt": run_attempt,
                "host": socket.gethostname(),
                "acquired_at": time.time(),
            }
        ).encode("utf-8")
        # Byte zero is the cross-platform lock. Keep readable owner metadata after it:
        # Windows byte-range locks reject reads that overlap the locked byte.
        handle.seek(1)
        handle.write(owner_metadata)
        handle.truncate(1 + len(owner_metadata))
        handle.flush()
        yield
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
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise RuntimeError("The repository execution lock owner cannot be verified.") from exc

    held_by_another_process = False
    try:
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
        if (
            not isinstance(owner, dict)
            or owner.get("pid") != os.getppid()
            or owner.get("run_id") != str(run_id)
            or owner.get("host") != socket.gethostname()
        ):
            raise RuntimeError("PR publication is not owned by the active cycle controller.")
    finally:
        handle.close()
