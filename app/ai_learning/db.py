"""Private SQLite store with checksummed, transactional migrations.

This store is separate from data/learning.db. Unknown existing schemas fail closed.
"""
from __future__ import annotations
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from .config import database_path

MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "ai-learning-initial", (
        """CREATE TABLE courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 300),
            provider TEXT NOT NULL DEFAULT 'manual' CHECK(length(provider) <= 80),
            model_name TEXT, language TEXT NOT NULL DEFAULT 'fa',
            target_level TEXT NOT NULL DEFAULT 'beginner_to_advanced',
            scope_notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','paused','completed','cancelled','archived')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            parent_topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            position INTEGER NOT NULL CHECK(position >= 1),
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 300),
            description TEXT NOT NULL DEFAULT '',
            approval_status TEXT NOT NULL DEFAULT 'pending' CHECK(approval_status IN ('pending','approved','rejected')),
            processing_status TEXT NOT NULL DEFAULT 'pending' CHECK(processing_status IN ('pending','queued','running','paused','received','needs_review','failed','skipped')),
            deleted_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(course_id, position)
        )""",
        """CREATE TABLE lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position >= 1),
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 300),
            status TEXT NOT NULL DEFAULT 'needs_review' CHECK(status IN ('draft','needs_review','ready','paused','failed','archived')),
            deleted_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(topic_id, position)
        )""",
        """CREATE TABLE content_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            version_no INTEGER NOT NULL CHECK(version_no >= 1),
            markdown TEXT NOT NULL, raw_response TEXT, content_sha256 TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'needs_review' CHECK(review_status IN ('needs_review','reviewed','rejected','published','superseded','withdrawn')),
            source_kind TEXT NOT NULL DEFAULT 'manual' CHECK(source_kind IN ('manual','chatgpt_ui','other_provider','import')),
            provider TEXT NOT NULL DEFAULT 'manual', model_name TEXT, run_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(lesson_id, version_no)
        )""",
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            lesson_id INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
            run_type TEXT NOT NULL CHECK(run_type IN ('outline','lesson','import')),
            selection_mode TEXT NOT NULL DEFAULT 'single' CHECK(selection_mode IN ('single','selected','all_approved')),
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','cancelled','needs_user_action')),
            prompt_text TEXT NOT NULL DEFAULT '', response_text TEXT,
            error_code TEXT, error_message TEXT, request_key TEXT UNIQUE, started_at TEXT, finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE run_topics (
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'queued', error_message TEXT,
            PRIMARY KEY(run_id, topic_id)
        )""",
        """CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_version_id INTEGER NOT NULL REFERENCES content_versions(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '', url TEXT, publisher TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'model_response', accessed_at TEXT,
            excerpt TEXT NOT NULL DEFAULT '', validation_status TEXT NOT NULL DEFAULT 'unverified'
              CHECK(validation_status IN ('unverified','reviewed','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_version_id INTEGER NOT NULL REFERENCES content_versions(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0), heading_path TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL, content_sha256 TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(content_version_id, ordinal)
        )""",
        """CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            to_lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL CHECK(relation_type IN ('prerequisite','related_to','extends','example_of','duplicate_of','contradicts','supersedes')),
            rationale TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'proposed' CHECK(review_status IN ('proposed','reviewed','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, CHECK(from_lesson_id <> to_lesson_id),
            UNIQUE(from_lesson_id, to_lesson_id, relation_type)
        )""",
        """CREATE TABLE query_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_sha256 TEXT NOT NULL,
            result_chunk_ids_json TEXT NOT NULL,
            result_count INTEGER NOT NULL CHECK(result_count >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX idx_ai_query_traces_created ON query_traces(created_at DESC, id DESC)",
        "CREATE INDEX idx_ai_topics_course_order ON topics(course_id, deleted_at, position)",
        "CREATE INDEX idx_ai_lessons_topic_order ON lessons(topic_id, deleted_at, position)",
        "CREATE INDEX idx_ai_versions_lesson_status ON content_versions(lesson_id, review_status, version_no DESC)",
        "CREATE INDEX idx_ai_chunks_active ON chunks(active, content_version_id)",
        "CREATE INDEX idx_ai_runs_course_status ON runs(course_id, status, id DESC)",
    )),
)

class StoreSchemaError(RuntimeError):
    """Raised instead of guessing how to modify an unknown existing database."""

def _checksum(version: int, name: str, statements: tuple[str, ...]) -> str:
    raw = "\n".join((str(version), name, *[statement.strip() for statement in statements]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def connect() -> sqlite3.Connection:
    path = Path(database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def initialize_store() -> None:
    """Create a new store or apply known migrations; never adopt unknown tables."""
    conn = connect()
    try:
        has_history = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_learning_migrations'").fetchone() is not None
        user_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        if not has_history and user_tables:
            raise StoreSchemaError("AI-learning database has tables but no migration history; no changes were made.")
        # Enable WAL only after confirming the store is new or has trusted migration history.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS ai_learning_migrations (
            version INTEGER PRIMARY KEY, migration_name TEXT NOT NULL, checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        applied = {int(r["version"]): (r["migration_name"], r["checksum"]) for r in conn.execute("SELECT version,migration_name,checksum FROM ai_learning_migrations")}
        for version, name, statements in MIGRATIONS:
            digest = _checksum(version, name, statements)
            if version in applied:
                if applied[version] != (name, digest):
                    raise StoreSchemaError(f"Migration {version} checksum mismatch; manual review required.")
                continue
            if applied and version <= max(applied):
                raise StoreSchemaError(f"Migration history is incomplete at version {version}.")
            for statement in statements:
                conn.execute(statement)
            conn.execute("INSERT INTO ai_learning_migrations(version,migration_name,checksum) VALUES(?,?,?)", (version,name,digest))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
