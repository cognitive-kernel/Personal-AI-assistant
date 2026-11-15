from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.autonomous_engineering_state import (
    exclusive_file_lock as _shared_exclusive_file_lock,
)


def exclusive_file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float,
    timeout_message: str,
):
    """Compatibility facade for the packaged shared OS file-lock primitive."""
    return _shared_exclusive_file_lock(
        lock_path,
        timeout_seconds=timeout_seconds,
        timeout_message=timeout_message,
    )
