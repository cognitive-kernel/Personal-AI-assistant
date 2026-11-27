"""Compatibility facade; the application package owns durable task operations."""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.autonomous_engineering_task_lifecycle import (
    ARCHIVE_DIR, TASK_FILE, OWNER_FILE, SCHEMA_VERSION, MAX_TASK_BYTES,
    LifecycleConflict, list_pending, list_archived, archive_task, restore_task,
)
