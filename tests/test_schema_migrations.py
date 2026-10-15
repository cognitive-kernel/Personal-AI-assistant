import sqlite3

import pytest

from app.schema_migrations import (
    Migration,
    MigrationError,
    MigrationRunner,
    UntrackedSchemaError,
    record_baseline,
    schema_fingerprint,
)


def connect(path):
    return sqlite3.connect(path, timeout=10)


def test_runner_applies_migrations_transactionally_and_is_idempotent(tmp_path):
    path = tmp_path / "fresh.db"
    migrations = MigrationRunner(
        (
            Migration(1, "create-records", ("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",)),
            Migration(2, "add-note", ("ALTER TABLE records ADD COLUMN note TEXT",)),
        )
    )
    with connect(path) as connection:
        assert migrations.apply(connection) == 2
        connection.execute("INSERT INTO records(value, note) VALUES ('kept', 'after migration')")
        connection.commit()
        assert migrations.apply(connection) == 2
        history = connection.execute(
            "SELECT version, migration_id FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(1, "create-records"), (2, "add-note")]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_runner_refuses_untracked_existing_schema(tmp_path):
    path = tmp_path / "legacy.db"
    with connect(path) as connection:
        connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY)")
        with pytest.raises(UntrackedSchemaError, match="no migration history"):
            MigrationRunner(
                (Migration(1, "create-records", ("CREATE TABLE records (id INTEGER PRIMARY KEY)",)),)
            ).apply(connection)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_explicit_baseline_requires_exact_schema_fingerprint_and_allows_forward_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with connect(path) as connection:
        connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY, value TEXT)")
        fingerprint = schema_fingerprint(connection)
        baseline = Migration(1, "create-legacy-records", (
            "CREATE TABLE legacy_records (id INTEGER PRIMARY KEY, value TEXT)",
        ))
        record_baseline(
            connection,
            migration=baseline,
            expected_fingerprint=fingerprint,
        )
        runner = MigrationRunner(
            (
                baseline,
                Migration(2, "add-index", ("CREATE INDEX idx_legacy_value ON legacy_records(value)",)),
            )
        )
        assert runner.apply(connection) == 2
        assert runner.apply(connection) == 2
        assert connection.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY version"
        ).fetchall() == [("create-legacy-records",), ("add-index",)]


def test_baseline_rejects_schema_fingerprint_mismatch_without_mutation(tmp_path):
    path = tmp_path / "legacy.db"
    with connect(path) as connection:
        connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY)")
        wrong_fingerprint = "0" * 64
        with pytest.raises(UntrackedSchemaError, match="does not match"):
            record_baseline(
                connection,
                migration=Migration(1, "create-legacy-records", (
                    "CREATE TABLE legacy_records (id INTEGER PRIMARY KEY)",
                )),
                expected_fingerprint=wrong_fingerprint,
            )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_runner_rejects_changed_checksum_for_applied_migration(tmp_path):
    path = tmp_path / "history.db"
    with connect(path) as connection:
        MigrationRunner(
            (Migration(1, "create-records", ("CREATE TABLE records (id INTEGER PRIMARY KEY)",)),)
        ).apply(connection)
        changed = MigrationRunner(
            (Migration(1, "create-records", ("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)",)),)
        )
        with pytest.raises(MigrationError, match="checksum changed"):
            changed.apply(connection)


def test_failed_migration_rolls_back_schema_and_history(tmp_path):
    path = tmp_path / "rollback.db"
    with connect(path) as connection:
        runner = MigrationRunner(
            (
                Migration(1, "create-records", ("CREATE TABLE records (id INTEGER PRIMARY KEY)",)),
                Migration(2, "invalid-change", ("CREATE TABLE temporary_table (id INTEGER)", "INSERT INTO missing_table VALUES (1)")),
            )
        )
        with pytest.raises(sqlite3.OperationalError):
            runner.apply(connection)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []
        assert not connection.in_transaction
        assert runner.migrations[0].version == 1
