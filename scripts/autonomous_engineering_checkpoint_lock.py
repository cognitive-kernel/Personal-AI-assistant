from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    from .autonomous_engineering_file_lock import exclusive_file_lock
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_file_lock import exclusive_file_lock


LOCK_FILE_NAME = "autonomous-checkpoint-store.lock"


@contextmanager
def checkpoint_store_lock(
    state_directory: Path,
    *,
    timeout_seconds: float = 10.0,
) -> Iterator[None]:
    """Own checkpoint-store mutation/lifetime using the shared OS lock primitive."""
    lock_path = Path(state_directory) / LOCK_FILE_NAME
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=max(0.0, timeout_seconds),
        timeout_message="Timed out waiting for exclusive autonomous checkpoint-store ownership.",
    ):
        yield
