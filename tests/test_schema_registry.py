import sqlite3

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
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 3
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in history] == [1, 2, 3]
        assert len(history[0][1]) == 64
        assert history[1][1] is None and history[2][1] is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_known_current_schema_is_baselined_without_replaying_migrations():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 3) as connection:
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 3
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(3, schema_fingerprint(connection))]


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
