"""Compatibility import for CLI scripts; app owns the checkpoint-store lock."""

from app.autonomous_engineering_checkpoint_lock import LOCK_FILE_NAME, checkpoint_store_lock

__all__ = ["LOCK_FILE_NAME", "checkpoint_store_lock"]
