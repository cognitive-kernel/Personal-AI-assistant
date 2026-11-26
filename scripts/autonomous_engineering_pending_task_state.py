"""Compatibility facade; the application package owns durable task operations."""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.autonomous_engineering_task_owner import (
    SCHEMA_VERSION, OWNER_FILENAME, pending_task_owner_path, task_digest,
    validate_owner_record as _validate_owner_record, claim_pending_task_owner_unlocked,
    reconcile_pending_task_owner_unlocked, validate_pending_task_owner_unlocked,
)
