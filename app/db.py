from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from .config import SETTING_DEFAULTS, defaults

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
                model_key = f"{prefix}_ollama_model"
                if (
                    model_key not in existing_settings
                    and os.getenv(f"{prefix.upper()}_OLLAMA_MODEL") is None
                ):
                    conn.execute(
                        "UPDATE settings SET value_json=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                        (json.dumps(existing_settings["ollama_model"]), model_key),
                    )


def get_settings() -> dict[str, Any]:
    with settings_connection() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}

def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError("Unsupported settings: " + ", ".join(sorted(unknown)))
    with settings_connection() as conn:
        for key, value in values.items():
            conn.execute("""INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP""",
                (key, json.dumps(value)))
    return get_settings()
