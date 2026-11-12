from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


LOCK_FILE_NAME = "autonomous-checkpoint-store.lock"


@contextmanager
def checkpoint_store_lock(
    state_directory: Path,
    *,
    timeout_seconds: float = 10.0,
) -> Iterator[None]:
    """Own the checkpoint store exclusively; the OS releases ownership on process exit."""
    state_directory.mkdir(parents=True, exist_ok=True)
    lock_path = state_directory / LOCK_FILE_NAME
    deadline = time.monotonic() + max(0.0, timeout_seconds)

    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        if os.name == "nt":
            import msvcrt

            acquired = False
            while not acquired:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for exclusive autonomous checkpoint-store ownership."
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        acquired = False
        while not acquired:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out waiting for exclusive autonomous checkpoint-store ownership."
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
