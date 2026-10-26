"""Feature gates for the optional AI-learning subsystem."""
from __future__ import annotations
import os

def _enabled(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}

def is_enabled() -> bool:
    return _enabled("AI_LEARNING_ENABLED", default=False)

def retrieval_is_enabled() -> bool:
    return is_enabled() and _enabled("AI_LEARNING_RETRIEVAL_ENABLED", default=False)

def database_path() -> str:
    return os.getenv("AI_LEARNING_DB_PATH", "data/ai_learning.db")
