"""Compatibility imports for CLI scripts; app owns shared state and lock implementations."""

from app.autonomous_engineering_execution_lock import repository_execution_lock
from app.autonomous_engineering_state import execution_state_directory

__all__ = ["repository_execution_lock", "execution_state_directory"]
