import sqlite3

import pytest

from app import db, task_manager
from app.schema_migrations import MigrationRunner, UntrackedSchemaError, schema_fingerprint
from app.schema_registry import (
    LEARNING_MIGRATIONS,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
    initialize_connection,
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
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5

        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_queue)")}
        assert {"quality_json", "path_topic_id"} <= job_columns
        assert {"dedupe_key", "checkpoint_json", "lease_owner", "lease_expires_at"} <= task_columns
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("retained", "queued")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]


def test_upgrade_from_version_four_preserves_task_and_adds_dedupe_key():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert MigrationRunner(LEARNING_MIGRATIONS[:4]).apply(connection) == 4
        task_id = connection.execute(
            "INSERT INTO task_queue(task_type, payload_json) VALUES ('learning.run_job', ?)",
            ('{"job_id": 7}',),
        ).lastrowid
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5
        task = connection.execute(
            "SELECT task_type, payload_json FROM task_queue WHERE id=?", (task_id,)
        ).fetchone()
        assert task == ("learning.run_job", '{"job_id": 7}')
        assert "dedupe_key" in {
            row[1] for row in connection.execute("PRAGMA table_info(task_queue)")
        }
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_registry_exposes_reviewable_canonical_fingerprints():
    settings_fingerprint = fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
    learning_v1 = fingerprint_for_version(LEARNING_MIGRATIONS, 1)
    learning_v2 = fingerprint_for_version(LEARNING_MIGRATIONS, 2)
    learning_fingerprints = [
        fingerprint_for_version(LEARNING_MIGRATIONS, version) for version in range(1, 6)
    ]

    assert len(settings_fingerprint) == 64
    assert all(len(value) == 64 for value in learning_fingerprints)
    assert len(set(learning_fingerprints)) == 5



def _create_untracked_schema(migrations, version):
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in migrations:
        if migration.version > version:
            break
        for statement in migration.statements:
            connection.execute(statement)
    connection.commit()
    return connection


def test_known_legacy_v1_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 1) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 5
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in history] == [1, 2, 3, 4, 5]
        assert len(history[0][1]) == 64
        assert all(row[1] is None for row in history[1:])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_known_current_schema_is_baselined_without_replaying_migrations():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 5) as connection:
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 5
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(5, schema_fingerprint(connection))]


def test_unknown_schema_is_not_mutated_or_auto_adopted():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)")
        connection.commit()
        with pytest.raises(UntrackedSchemaError, match="does not match"):
            initialize_connection(connection, LEARNING_MIGRATIONS)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='unknown_table'"
        ).fetchone() == ("unknown_table",)


def test_schema_fingerprint_ignores_column_order_but_preserves_constraints():
    with sqlite3.connect(":memory:") as first, sqlite3.connect(":memory:") as second:
        first.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
            "right_value INTEGER, CHECK(right_value >= 0))"
        )
        second.execute(
            "CREATE TABLE IF NOT EXISTS sample (right_value INTEGER, left_value TEXT, "
            "id INTEGER PRIMARY KEY, CHECK (right_value >= 0))"
        )
        assert schema_fingerprint(first) == schema_fingerprint(second)

    with sqlite3.connect(":memory:") as different:
        different.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
            "right_value INTEGER, CHECK(right_value > 0))"
        )
        with sqlite3.connect(":memory:") as reference:
            reference.execute(
                "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
                "right_value INTEGER, CHECK(right_value >= 0))"
            )
            assert schema_fingerprint(different) != schema_fingerprint(reference)



def test_current_application_schema_matches_registered_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()

    with db.settings_connection() as connection:
        assert schema_fingerprint(connection) == fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
        assert initialize_connection(connection, SETTINGS_MIGRATIONS) == 1
    with db.learning_connection() as connection:
        assert schema_fingerprint(connection) == fingerprint_for_version(LEARNING_MIGRATIONS, 5)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 5



def test_legacy_learning_database_without_task_queue_upgrades_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as connection:
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('pre-queue', 'queued', 'test')"
        ).lastrowid
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_queue'"
        ).fetchone() is None
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 5
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("pre-queue", "queued")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
