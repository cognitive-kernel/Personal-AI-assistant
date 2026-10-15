"""Reviewed, versioned schema definitions for fresh SQLite installations.

The registry is intentionally not wired into application startup yet. Legacy
adoption requires comparing a live database against reviewed fingerprints.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from .schema_migrations import Migration, MigrationRunner, schema_fingerprint


SETTINGS_MIGRATIONS = (
    Migration(
        1,
        "settings-initial",
        (
            """CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        ),
    ),
)

LEARNING_MIGRATIONS = (
    Migration(
        1,
        "learning-initial",
        (
            """CREATE TABLE learning_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                curriculum_slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_path_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                position INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                latest_score REAL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(path_id, topic),
                UNIQUE(path_id, position)
            )""",
            """CREATE TABLE learning_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                lesson TEXT,
                assessment_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                evidence_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                content_hash TEXT,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_source_text (
                job_id INTEGER PRIMARY KEY REFERENCES learning_jobs(id) ON DELETE CASCADE,
                content TEXT NOT NULL
            )""",
            """CREATE TABLE knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'unverified',
                job_id INTEGER NOT NULL UNIQUE REFERENCES learning_jobs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(topic, version)
            )""",
            """CREATE TABLE assessment_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                answers_json TEXT NOT NULL,
                score REAL NOT NULL,
                correct_count INTEGER NOT NULL,
                question_count INTEGER NOT NULL,
                scoring_method TEXT NOT NULL DEFAULT 'normalized_exact_match',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE lesson_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                outcomes_json TEXT NOT NULL,
                value_rating INTEGER NOT NULL CHECK(value_rating BETWEEN 1 AND 5),
                takeaway TEXT NOT NULL DEFAULT '',
                issues TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL CHECK(length(trim(task_type)) > 0),
                payload_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','pause_requested','paused',
                                     'cancel_requested','cancelled','succeeded','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK(max_attempts >= 1),
                checkpoint_json TEXT,
                result_json TEXT,
                error TEXT,
                scheduled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                lease_owner TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_learning_jobs_status ON learning_jobs(status)",
            "CREATE INDEX idx_knowledge_topic ON knowledge_items(topic, version DESC)",
            "CREATE INDEX idx_assessment_attempts_job ON assessment_attempts(job_id, id DESC)",
            "CREATE INDEX idx_lesson_reviews_job ON lesson_reviews(job_id, id DESC)",
            "CREATE INDEX idx_path_topics_status ON learning_path_topics(path_id, status, position)",
            "CREATE INDEX idx_task_queue_ready ON task_queue(status, scheduled_at, priority DESC, id)",
            "CREATE INDEX idx_task_queue_lease ON task_queue(status, lease_expires_at)",
        ),
    ),
    Migration(
        2,
        "learning-job-quality-json",
        ("ALTER TABLE learning_jobs ADD COLUMN quality_json TEXT",),
    ),
    Migration(
        3,
        "learning-path-topic-and-task-idempotency",
        (
            "ALTER TABLE learning_jobs ADD COLUMN path_topic_id INTEGER REFERENCES learning_path_topics(id) ON DELETE SET NULL",
            "ALTER TABLE task_queue ADD COLUMN dedupe_key TEXT",
            "CREATE INDEX idx_learning_jobs_path_topic ON learning_jobs(path_topic_id, status)",
            "CREATE UNIQUE INDEX idx_task_queue_dedupe_key ON task_queue(dedupe_key) WHERE dedupe_key IS NOT NULL",
        ),
    ),
)


def fingerprint_for_version(migrations: Iterable[Migration], version: int) -> str:
    """Build a canonical schema fixture in memory and return its exact fingerprint."""
    selected = tuple(migration for migration in migrations if migration.version <= version)
    if not selected or selected[-1].version != version:
        raise ValueError(f"No migration registry reaches version {version}.")
    with sqlite3.connect(":memory:") as connection:
        MigrationRunner(selected).apply(connection)
        return schema_fingerprint(connection)
