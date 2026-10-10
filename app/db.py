from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any
from .config import SETTING_DEFAULTS, defaults

def _connect(path: str) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def settings_connection() -> sqlite3.Connection:
    return _connect(defaults.settings_db_path)

def learning_connection() -> sqlite3.Connection:
    return _connect(defaults.learning_db_path)

def init_databases() -> None:
    with settings_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        for key, value in SETTING_DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)", (key, json.dumps(value)))
    with learning_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, status TEXT NOT NULL,
            source_kind TEXT NOT NULL, lesson TEXT, assessment_json TEXT, error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            evidence_type TEXT NOT NULL, source_ref TEXT NOT NULL, content_hash TEXT,
            note TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_source_text (
            job_id INTEGER PRIMARY KEY REFERENCES learning_jobs(id) ON DELETE CASCADE,
            content TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_jobs_status ON learning_jobs(status)")

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
