from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _default_state_dir() -> Path:
    try:
        from .autonomous_engineering_state_paths import execution_state_directory
    except ImportError:  # Direct script execution from the scripts directory.
        from autonomous_engineering_state_paths import execution_state_directory
    return execution_state_directory()


@contextmanager
def durable_state_lock(
    state_dir: Path | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[None]:
    """Cross-process, cross-session lock shared by Python and PowerShell.

    The lock is a byte-range lock on a file inside the configured shared state
    directory. Unlike a named Windows mutex, it has no session namespace and
    does not inherit an ACL from the identity that first creates a kernel object.
    The directory ACL is provisioned independently by the installer.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds < 0
    ):
        raise ValueError("Lock timeout must be a non-negative number.")
    timeout = float(timeout_seconds)
    state_dir = Path(state_dir) if state_dir is not None else _default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "autonomous-durable-state.lock"
    deadline = time.monotonic() + timeout

    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for the shared durable-state file lock."
                        ) from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out waiting for the shared durable-state file lock."
                    ) from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
