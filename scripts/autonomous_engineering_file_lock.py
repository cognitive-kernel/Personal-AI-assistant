from __future__ import annotations

import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("File-lock timeout must be a finite non-negative number.")
    try:
        timeout = float(timeout_seconds)
    except (OverflowError, ValueError) as exc:
        raise ValueError("File-lock timeout must be a finite non-negative number.") from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("File-lock timeout must be a finite non-negative number.")
    return timeout


@contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float,
    timeout_message: str,
) -> Iterator[None]:
    """Acquire an OS-managed exclusive byte-zero lock, released on process exit.

    The persistent file is only the cross-process rendezvous point. Ownership is
    represented by the kernel lock, never by file existence or file contents.
    Windows uses the byte-range contract shared with PowerShell; POSIX uses flock.
    """
    timeout = _validate_timeout(timeout_seconds)
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        if os.name == "nt":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(timeout_message) from exc
                    time.sleep(min(0.05, remaining))
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(timeout_message) from exc
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
