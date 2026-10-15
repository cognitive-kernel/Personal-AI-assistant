from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from .config import SETTING_DEFAULTS, defaults

logger = logging.getLogger("personal_ai_assistant.db")

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
    """Initialize schemas through versioned migrations and seed defaults idempotently."""
    from .schema_registry import (
        LEARNING_MIGRATIONS,
        SETTINGS_MIGRATIONS,
        initialize_connection,
    )

    backup_state = {"created": False}

    def backup_before_upgrade() -> None:
        if backup_state["created"]:
            return
        from .db_backup import backup_databases

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = (
            Path(defaults.settings_db_path).expanduser().resolve().parent
            / "backups"
            / f"pre-migration-{timestamp}-{uuid4().hex[:8]}"
        )
        manifest = backup_databases(
            defaults.settings_db_path,
            defaults.learning_db_path,
            backup_dir,
        )
        backup_state["created"] = True
        logger.info(
            "Created verified local backup before schema upgrade: %s (%s)",
            backup_dir,
            manifest["created_at"],
        )

    with settings_connection() as conn:
        initialize_connection(
            conn,
            SETTINGS_MIGRATIONS,
            before_upgrade=backup_before_upgrade,
        )
        for key, value in SETTING_DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

    with learning_connection() as conn:
        initialize_connection(
            conn,
            LEARNING_MIGRATIONS,
            before_upgrade=backup_before_upgrade,
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
