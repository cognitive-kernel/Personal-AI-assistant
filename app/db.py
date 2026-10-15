from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any
from .config import SETTING_DEFAULTS, defaults
from .migrations import SchemaMigrationError, run_migrations, sql_migration

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

_SETTINGS_MIGRATIONS = (
    sql_migration(
        1,
        "settings.initial",
        """CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ),
)


def _is_current_settings_schema(conn: sqlite3.Connection) -> bool:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    } - {"schema_migrations"}
    if tables != {"settings"}:
        return False
    columns = {
        row[1]: {
            "type": str(row[2]).upper(),
            "not_null": int(row[3]),
            "default": row[4],
            "primary_key": int(row[5]),
        }
        for row in conn.execute("PRAGMA table_info(settings)").fetchall()
    }
    return (
        set(columns) == {"key", "value_json", "updated_at"}
        and columns["key"]["type"] == "TEXT"
        and columns["key"]["primary_key"] == 1
        and columns["value_json"]["type"] == "TEXT"
        and columns["value_json"]["not_null"] == 1
        and columns["updated_at"]["type"] == "TEXT"
        and columns["updated_at"]["not_null"] == 1
        and str(columns["updated_at"]["default"]).upper() == "CURRENT_TIMESTAMP"
    )


def _detect_legacy_settings_schema(conn: sqlite3.Connection) -> int | None:
    """Recognize only the exact unversioned settings schema shipped previously."""
    return 1 if _is_current_settings_schema(conn) else None


def init_databases() -> None:
    with settings_connection() as conn:
        run_migrations(
            conn,
            _SETTINGS_MIGRATIONS,
            legacy_version_detector=_detect_legacy_settings_schema,
            schema_validator=_is_current_settings_schema,
        )
        for key, value in SETTING_DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)", (key, json.dumps(value)))
    with learning_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, status TEXT NOT NULL,
            source_kind TEXT NOT NULL, lesson TEXT, assessment_json TEXT, quality_json TEXT, error TEXT,
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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(learning_jobs)").fetchall()}
        if "quality_json" not in columns:
            conn.execute("ALTER TABLE learning_jobs ADD COLUMN quality_json TEXT")
        if "path_topic_id" not in columns:
            conn.execute("ALTER TABLE learning_jobs ADD COLUMN path_topic_id INTEGER REFERENCES learning_path_topics(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_jobs_status ON learning_jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_jobs_path_topic ON learning_jobs(path_topic_id, status)")
        conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, content TEXT NOT NULL,
            source_kind TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unverified',
            job_id INTEGER NOT NULL UNIQUE REFERENCES learning_jobs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(topic, version))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge_items(topic, version DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS assessment_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            answers_json TEXT NOT NULL, score REAL NOT NULL,
            correct_count INTEGER NOT NULL, question_count INTEGER NOT NULL,
            scoring_method TEXT NOT NULL DEFAULT 'normalized_exact_match',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assessment_attempts_job ON assessment_attempts(job_id, id DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS lesson_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            outcomes_json TEXT NOT NULL,
            value_rating INTEGER NOT NULL CHECK(value_rating BETWEEN 1 AND 5),
            takeaway TEXT NOT NULL DEFAULT '',
            issues TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lesson_reviews_job ON lesson_reviews(job_id, id DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            curriculum_slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_path_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
            topic TEXT NOT NULL, position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            latest_score REAL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(path_id, topic), UNIQUE(path_id, position))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_path_topics_status ON learning_path_topics(path_id, status, position)")

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
