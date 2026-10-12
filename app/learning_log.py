"""Structured diagnostics for the learning and experience loops.

The log is local, rotating, and intentionally excludes prompts and source text by default.
Override LEARNING_LOG_PATH to choose a different location.
"""
from __future__ import annotations

import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOGGER_NAME = "personal_ai_assistant.learning"
_MAX_FIELD_CHARS = 1200
_SECRET_PARTS = ("password", "token", "secret", "api_key", "authorization", "cookie")


def log_path() -> Path:
    configured = os.getenv("LEARNING_LOG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "data" / "logs" / "learning.log"


def configure_learning_logger() -> Path:
    """Create the log file and configure a bounded rotating file handler."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure a newly configured path has a real, shareable file even before the first error.
    path.touch(exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target = str(path)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == str(Path(target).resolve())
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(
            target, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return path


def _sanitize_text(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)([?&](?:access_token|refresh_token|api_key|token|key|secret|password|authorization)=)[^&\s]+", r"\1[REDACTED]", value)
    return value[:_MAX_FIELD_CHARS]

def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in context.items():
        normalized = key.casefold()
        if any(part in normalized for part in _SECRET_PARTS):
            safe[key] = "[REDACTED]"
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [str(item)[:200] for item in value[:20]]
        elif isinstance(value, dict):
            safe[key] = "[object omitted]"
        else:
            safe[key] = _sanitize_text(str(value))
    return safe


def log_event(
    event: str,
    *,
    level: int = logging.ERROR,
    exc_info: bool = False,
    **context: Any,
) -> None:
    """Write one JSON record; never let diagnostics break the main application."""
    try:
        configure_learning_logger()
        record = {
            "event": str(event)[:160],
            "context": _safe_context(context),
        }
        logger = logging.getLogger(_LOGGER_NAME)
        if exc_info:
            import traceback
            record["traceback"] = traceback.format_exc()[-8000:]
        logger.log(level, json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        # Logging is best effort and must never mask the original application error.
        pass


def diagnostics() -> dict[str, Any]:
    path = configure_learning_logger()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "exists": path.is_file(), "size_bytes": size}
