"""Compatibility import for CLI scripts; the app package owns the lock implementation."""

from app.autonomous_engineering_execution_lock import repository_execution_lock

__all__ = ["repository_execution_lock"]
