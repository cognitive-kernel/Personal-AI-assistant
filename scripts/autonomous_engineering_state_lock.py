from __future__ import annotations

from pathlib import Path
from typing import Iterator

from app.autonomous_engineering_state import (
    durable_state_lock as _shared_durable_state_lock,
)


def _default_state_dir() -> Path:
    try:
        from .autonomous_engineering_state_paths import execution_state_directory
    except ImportError:  # Direct script execution from the scripts directory.
        from autonomous_engineering_state_paths import execution_state_directory
    return execution_state_directory()


def durable_state_lock(
    state_dir: Path | None = None,
    *,
    timeout_seconds: float = 30.0,
):
    """Compatibility facade for the shared byte-range lock.

    The OS lock is held on autonomous-durable-state.lock and is shared with
    PowerShell through the same byte offset and length.
    """
    return _shared_durable_state_lock(
        state_dir,
        timeout_seconds=timeout_seconds,
        default_state_dir_fn=_default_state_dir,
    )
