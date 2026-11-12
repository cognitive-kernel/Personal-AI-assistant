from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    from .autonomous_engineering_state_paths import execution_state_directory
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_state_paths import execution_state_directory



@contextmanager
def repository_execution_lock(run_id: str, run_attempt: str) -> Iterator[None]:
    """Own the repository worktree exclusively; the OS releases ownership after process death."""
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
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "run_id": run_id,
                    "run_attempt": run_attempt,
                    "host": socket.gethostname(),
                    "acquired_at": time.time(),
                }
            ).encode("utf-8")
        )
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
