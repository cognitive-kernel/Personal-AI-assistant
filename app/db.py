from __future__ import annotations
import json
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
    with settings_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
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
        conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            content_hash TEXT NOT NULL, license_note TEXT NOT NULL DEFAULT 'Reuse terms not independently reviewed; retain source attribution.',
            status TEXT NOT NULL DEFAULT 'indexed', fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
            embedding_json TEXT, embedding_model TEXT,
            UNIQUE(source_id, chunk_index))""")
        chunk_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_chunks)").fetchall()}
        if "embedding_json" not in chunk_columns:
            conn.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding_json TEXT")
        if "embedding_model" not in chunk_columns:
            conn.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding_model TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_id, chunk_index)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(content)")

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
