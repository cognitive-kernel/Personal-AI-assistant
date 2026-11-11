from __future__ import annotations
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any
from .config import SETTING_DEFAULTS, defaults

_RUNTIME_SETTINGS_LOCK = threading.RLock()
_RUNTIME_SETTINGS_OVERRIDES: dict[str, Any] = {}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect(path: str) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def settings_connection() -> sqlite3.Connection:
    return _connect(defaults.settings_db_path)

def learning_connection() -> sqlite3.Connection:
    return _connect(defaults.learning_db_path)

def init_databases() -> None:
    """Initialize versioned settings/learning stores and seed missing default settings."""
    from .schema_registry import (
        LEARNING_MIGRATIONS,
        SETTINGS_MIGRATIONS,
        initialize_database_file,
    )

    initialize_database_file(defaults.settings_db_path, SETTINGS_MIGRATIONS)
    initialize_database_file(defaults.learning_db_path, LEARNING_MIGRATIONS)

    with settings_connection() as conn:
        existing_rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
        existing_settings = {
            row["key"]: json.loads(row["value_json"]) for row in existing_rows
        }
        for key, value in SETTING_DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        # Preserve a user's pre-profile model and resource choices during upgrade.
        # Explicit per-purpose environment overrides remain authoritative.
        if "ollama_model" in existing_settings:
            legacy_to_profile = {
                "ollama_num_ctx": "ollama_num_ctx",
                "ollama_num_thread": "ollama_num_thread",
                "ollama_timeout_seconds": "ollama_timeout_seconds",
            }
            for prefix in ("chat", "learning", "programming"):
                for suffix, legacy_key in legacy_to_profile.items():
                    profile_key = f"{prefix}_{suffix}"
                    if profile_key not in existing_settings and legacy_key in existing_settings:
                        conn.execute(
                            "UPDATE settings SET value_json=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                            (json.dumps(existing_settings[legacy_key]), profile_key),
                        )
            # Keep the legacy model choice for chat. Learning and programming
            # get their own purpose-specific defaults unless explicitly configured.
            if (
                "chat_ollama_model" not in existing_settings
                and os.getenv("CHAT_OLLAMA_MODEL") is None
            ):
                conn.execute(
                    "UPDATE settings SET value_json=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                    (json.dumps(existing_settings["ollama_model"]), "chat_ollama_model"),
                )
            # Move only the former built-in learning default. Preserve any custom
            # model choice and honor an explicit environment override.
            learning_model_override = os.getenv("LEARNING_OLLAMA_MODEL")
            if isinstance(learning_model_override, str) and learning_model_override.strip():
                conn.execute(
                    "UPDATE settings SET value_json=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                    (json.dumps(learning_model_override.strip()), "learning_ollama_model"),
                )
            elif existing_settings.get("learning_ollama_model") == "qwen3:8b":
                conn.execute(
                    "UPDATE settings SET value_json=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                    (json.dumps("gpt-oss:20b"), "learning_ollama_model"),
                )


def get_settings() -> dict[str, Any]:
    with settings_connection() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
    settings = {row["key"]: json.loads(row["value_json"]) for row in rows}
    with _RUNTIME_SETTINGS_LOCK:
        settings.update(_RUNTIME_SETTINGS_OVERRIDES)
    return settings


def update_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Apply validated settings until process restart without writing them to disk."""
    unknown = set(values) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError("Unsupported settings: " + ", ".join(sorted(unknown)))
    with _RUNTIME_SETTINGS_LOCK:
        _RUNTIME_SETTINGS_OVERRIDES.update(values)
    return get_settings()


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError("Unsupported settings: " + ", ".join(sorted(unknown)))
    with settings_connection() as conn:
        for key, value in values.items():
            conn.execute("""INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP""",
                (key, json.dumps(value)))
    with _RUNTIME_SETTINGS_LOCK:
        for key in values:
            _RUNTIME_SETTINGS_OVERRIDES.pop(key, None)
    return get_settings()
