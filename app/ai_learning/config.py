"""Settings-backed feature controls for the isolated AI-learning subsystem."""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from ..config import SETTING_DEFAULTS

_DEFAULTS: dict[str, bool] = {
    "ai_learning_enabled": True,
    "ai_learning_retrieval_enabled": True,
    "ai_learning_browser_enabled": True,
}


def _setting(name: str) -> bool:
    """Read one validated persisted setting; use defaults only before settings DB bootstrap."""
    try:
        from ..db import get_settings

        settings: dict[str, Any] = get_settings()
    except sqlite3.OperationalError as exc:
        # Unit-level callers may inspect configuration before app startup has
        # initialized the settings schema. Do not hide corruption or other DB errors.
        if "no such table: settings" not in str(exc).casefold():
            raise
        settings = {}
    value = settings.get(name, _DEFAULTS[name])
    if type(value) is not bool:
        raise RuntimeError(f"Setting {name} must be a boolean.")
    return value


def is_enabled() -> bool:
    """Whether the AI-learning subsystem is enabled (enabled in a fresh install)."""
    return _setting("ai_learning_enabled")


def retrieval_is_enabled() -> bool:
    """Whether published AI-learning knowledge may participate in retrieval."""
    return is_enabled() and _setting("ai_learning_retrieval_enabled")


def browser_is_enabled() -> bool:
    """Whether the visible-browser provider adapter is enabled."""
    return is_enabled() and _setting("ai_learning_browser_enabled")


def max_topics_per_run() -> int:
    """Return the configured upper bound for one browser lesson run."""
    from ..db import get_settings

    try:
        settings = get_settings()
    except sqlite3.OperationalError as exc:
        if "no such table: settings" not in str(exc).casefold():
            raise
        settings = {}
    value = settings.get("ai_learning_lesson_batch_size", SETTING_DEFAULTS["ai_learning_lesson_batch_size"])
    if type(value) is not int or not 1 <= value <= 20:
        raise RuntimeError("Setting ai_learning_lesson_batch_size is outside its validated range.")
    return value


def database_path() -> str:
    """Return the isolated AI-learning database path."""
    return os.getenv("AI_LEARNING_DB_PATH", "data/ai_learning.db")
