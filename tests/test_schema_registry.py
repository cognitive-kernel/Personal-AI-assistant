import sqlite3

from app.schema_registry import LEARNING_MIGRATIONS, SETTINGS_MIGRATIONS


def _tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def test_fresh_settings_registry_creates_versioned_settings_schema():
    connection = sqlite3.connect(":memory:")
    try:
        assert SETTINGS_MIGRATIONS.apply(connection) == 1
        assert SETTINGS_MIGRATIONS.apply(connection) == 1
        assert _tables(connection) == {"settings", "schema_migrations"}
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(settings)")
        } == {"key", "value_json", "updated_at"}
        assert connection.execute(
            "SELECT version, migration_id FROM schema_migrations"
        ).fetchall() == [(1, "settings.create")]
    finally:
        connection.close()


def test_fresh_learning_registry_creates_current_learning_and_task_schema():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        assert LEARNING_MIGRATIONS.apply(connection) == 4
        assert LEARNING_MIGRATIONS.apply(connection) == 4
        assert {
            "learning_jobs",
            "learning_evidence",
            "learning_source_text",
            "knowledge_items",
            "assessment_attempts",
            "lesson_reviews",
            "learning_paths",
            "learning_path_topics",
            "task_queue",
            "schema_migrations",
        } <= _tables(connection)
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")
        }
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(task_queue)")
        }
        assert {"quality_json", "path_topic_id"} <= job_columns
        assert {"dedupe_key", "checkpoint_json", "lease_owner", "lease_expires_at"} <= task_columns
        assert connection.execute(
            "SELECT version, migration_id FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1, "learning.core"),
            (2, "learning.add-path-topic-reference"),
            (3, "tasks.create-queue"),
            (4, "tasks.add-dedupe-key"),
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
