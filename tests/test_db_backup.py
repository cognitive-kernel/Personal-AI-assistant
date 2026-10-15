import json
import sqlite3

import pytest

from app import db, task_manager
from app.db_backup import BackupRestoreError, backup_databases, restore_databases


@pytest.fixture
def initialized_databases(tmp_path, monkeypatch):
    settings_path = tmp_path / "live" / "settings.db"
    learning_path = tmp_path / "live" / "learning.db"
    monkeypatch.setattr(db.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(learning_path))
    db.init_databases()
    task_manager.initialize_task_store()
    return settings_path, learning_path


def test_schema_contracts_for_settings_learning_and_task_queue(initialized_databases):
    settings_path, learning_path = initialized_databases
    with sqlite3.connect(settings_path) as connection:
        settings_columns = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
        assert {"key", "value_json", "updated_at"} <= settings_columns
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    with sqlite3.connect(learning_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
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
        } <= tables
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_queue)")}
        assert {"quality_json", "path_topic_id"} <= job_columns
        assert {"dedupe_key", "checkpoint_json", "lease_owner", "lease_expires_at"} <= task_columns
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(task_queue)")}
        assert "idx_task_queue_dedupe_key" in indexes
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_and_restore_preserve_both_databases(initialized_databases, tmp_path):
    settings_path, learning_path = initialized_databases
    with db.settings_connection() as connection:
        connection.execute(
            "INSERT INTO settings(key, value_json) VALUES ('backup_test', 'true')"
        )
    with db.learning_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('backup test', 'queued', 'test')"
        )
        job_id = int(cursor.lastrowid)
    task = task_manager.create_task(
        "test.backup", {"job_id": job_id}, dedupe_key=f"backup-test:{job_id}"
    )

    bundle = tmp_path / "backups" / "snapshot-001"
    manifest = backup_databases(settings_path, learning_path, bundle)
    assert manifest["format_version"] == 1
    assert set(manifest["databases"]) == {"settings", "learning"}
    assert (bundle / "manifest.json").is_file()

    restored_dir = tmp_path / "restored"
    restored = restore_databases(bundle, restored_dir)
    assert set(restored) == {"settings", "learning"}
    with sqlite3.connect(restored["settings"]) as connection:
        assert connection.execute(
            "SELECT value_json FROM settings WHERE key='backup_test'"
        ).fetchone()[0] == "true"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(restored["learning"]) as connection:
        assert connection.execute(
            "SELECT id FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone()[0] == job_id
        saved_task = connection.execute(
            "SELECT task_type, dedupe_key FROM task_queue WHERE id=?", (task["id"],)
        ).fetchone()
        assert saved_task == ("test.backup", f"backup-test:{job_id}")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_rejects_modified_backup_file(initialized_databases, tmp_path):
    settings_path, learning_path = initialized_databases
    bundle = tmp_path / "backup"
    backup_databases(settings_path, learning_path, bundle)
    with (bundle / "settings.sqlite3").open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(BackupRestoreError, match="checksum"):
        restore_databases(bundle, tmp_path / "restored")


def test_backup_and_restore_never_overwrite_existing_destinations(initialized_databases, tmp_path):
    settings_path, learning_path = initialized_databases
    bundle = tmp_path / "backup"
    backup_databases(settings_path, learning_path, bundle)
    with pytest.raises(FileExistsError):
        backup_databases(settings_path, learning_path, bundle)
    with pytest.raises(FileExistsError):
        restore_databases(bundle, bundle)


def test_backup_requires_distinct_database_paths(tmp_path):
    same_path = tmp_path / "same.db"
    with sqlite3.connect(same_path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="distinct"):
        backup_databases(same_path, same_path, tmp_path / "backup")
