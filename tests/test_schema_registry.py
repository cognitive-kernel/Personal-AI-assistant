import sqlite3

from app.schema_migrations import MigrationRunner
from app.schema_registry import (
    LEARNING_MIGRATIONS,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
)


def test_settings_schema_can_be_created_from_versioned_migrations():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(SETTINGS_MIGRATIONS)
        assert runner.apply(connection) == 1
        assert runner.apply(connection) == 1
        columns = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
        assert {"key", "value_json", "updated_at"} <= columns
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_learning_schema_migrates_from_v1_without_losing_rows():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert MigrationRunner(LEARNING_MIGRATIONS[:1]).apply(connection) == 1
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('retained', 'queued', 'test')"
        ).lastrowid
        task_id = connection.execute(
            "INSERT INTO task_queue(task_type, payload_json) VALUES ('learning.run_job', ?)",
            ('{"job_id": 1}',),
        ).lastrowid
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 3
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 3

        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_queue)")}
        assert {"quality_json", "path_topic_id"} <= job_columns
        assert "dedupe_key" in task_columns
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("retained", "queued")
        assert connection.execute(
            "SELECT task_type, payload_json FROM task_queue WHERE id=?", (task_id,)
        ).fetchone() == ("learning.run_job", '{"job_id": 1}')
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(task_queue)")}
        assert "idx_task_queue_dedupe_key" in indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]


def test_registry_exposes_reviewable_canonical_fingerprints():
    settings_fingerprint = fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
    learning_v1 = fingerprint_for_version(LEARNING_MIGRATIONS, 1)
    learning_v2 = fingerprint_for_version(LEARNING_MIGRATIONS, 2)
    learning_v3 = fingerprint_for_version(LEARNING_MIGRATIONS, 3)

    assert len(settings_fingerprint) == 64
    assert len(learning_v1) == len(learning_v2) == len(learning_v3) == 64
    assert len({learning_v1, learning_v2, learning_v3}) == 3
