import json
import sqlite3

import pytest

from app import db
from app.migrations import Migration, SchemaMigrationError, run_migrations


def test_fresh_settings_database_records_versioned_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    db.init_databases()

    with db.settings_connection() as conn:
        row = conn.execute(
            "SELECT version, migration_id, checksum, applied_mode FROM schema_migrations"
        ).fetchone()
    assert row["version"] == 1
    assert row["migration_id"] == "settings.initial"
    assert len(row["checksum"]) == 64
    assert row["applied_mode"] == "migration"
    assert db.get_settings()["ollama_model"]


def test_exact_legacy_settings_schema_is_adopted_without_overwriting_values(
    tmp_path, monkeypatch
):
    settings_path = tmp_path / "settings.db"
    learning_path = tmp_path / "learning.db"
    monkeypatch.setattr(db.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(learning_path))
    with sqlite3.connect(settings_path) as conn:
        conn.execute(
            """CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "INSERT INTO settings(key, value_json) VALUES (?, ?)",
            ("ollama_model", json.dumps("legacy-user-model")),
        )

    db.init_databases()

    assert db.get_settings()["ollama_model"] == "legacy-user-model"
    with db.settings_connection() as conn:
        row = conn.execute(
            "SELECT version, migration_id, applied_mode FROM schema_migrations"
        ).fetchone()
    assert row["version"] == 1
    assert row["migration_id"] == "settings.initial"
    assert row["applied_mode"] == "baseline"


def test_unknown_unversioned_settings_schema_fails_without_rewriting_it(
    tmp_path, monkeypatch
):
    settings_path = tmp_path / "settings.db"
    monkeypatch.setattr(db.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    with sqlite3.connect(settings_path) as conn:
        conn.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO settings(key, value_json) VALUES ('preserve-me', 'true')"
        )

    with pytest.raises(SchemaMigrationError, match="recognized legacy baseline"):
        db.init_databases()

    with sqlite3.connect(settings_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "schema_migrations" not in tables
        assert conn.execute(
            "SELECT value_json FROM settings WHERE key='preserve-me'"
        ).fetchone()[0] == "true"


def test_migration_checksum_drift_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.settings_connection() as conn:
        conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")

    with pytest.raises(SchemaMigrationError, match="history mismatch"):
        db.init_databases()


def test_failed_migration_rolls_back_schema_changes():
    connection = sqlite3.connect(":memory:")

    def fail_after_ddl(conn):
        conn.execute("CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)")
        raise RuntimeError("simulated migration failure")

    migration = Migration(1, "test.rollback", "stable-test-checksum", fail_after_ddl)
    with pytest.raises(SchemaMigrationError, match="transaction rolled back"):
        run_migrations(connection, [migration])

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "should_rollback" not in tables
    connection.close()
