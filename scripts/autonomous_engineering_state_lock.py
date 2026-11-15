from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    from .autonomous_engineering_file_lock import exclusive_file_lock
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_file_lock import exclusive_file_lock


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
    """Cross-process, cross-session lock shared by Python and PowerShell."""
    directory = Path(state_dir) if state_dir is not None else _default_state_dir()
    lock_path = directory / "autonomous-durable-state.lock"
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=timeout_seconds,
        timeout_message="Timed out waiting for the shared durable-state file lock.",
    ):
        yield
