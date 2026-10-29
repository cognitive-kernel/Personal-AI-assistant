import sqlite3

import pytest

from app import cloud_resources, db, task_manager
from app.db_backup import BackupRestoreError, backup_databases, restore_databases


@pytest.fixture
def initialized_databases(tmp_path, monkeypatch):
    settings_path = tmp_path / "live" / "settings.db"
    learning_path = tmp_path / "live" / "learning.db"
    cloud_path = tmp_path / "live" / "cloud_resources.db"
    monkeypatch.setattr(db.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(learning_path))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(cloud_path))
    db.init_databases()
    cloud_resources.initialize_store()
    task_manager.initialize_task_store()
    return settings_path, learning_path, cloud_path


def test_backup_and_restore_preserve_both_databases(initialized_databases, tmp_path):
    settings_path, learning_path, _cloud_path = initialized_databases
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
    settings_path, learning_path, _cloud_path = initialized_databases
    bundle = tmp_path / "backup"
    backup_databases(settings_path, learning_path, bundle)
    with (bundle / "settings.sqlite3").open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(BackupRestoreError, match="checksum"):
        restore_databases(bundle, tmp_path / "restored")


def test_backup_and_restore_never_overwrite_existing_destinations(initialized_databases, tmp_path):
    settings_path, learning_path, _cloud_path = initialized_databases
    bundle = tmp_path / "backup"
    backup_databases(settings_path, learning_path, bundle)
    with pytest.raises(FileExistsError):
        backup_databases(settings_path, learning_path, bundle)
    # A destination equal to (or nested under) the source bundle is an
    # invalid restore layout, not an overwrite attempt.
    with pytest.raises(ValueError, match="outside the backup directory"):
        restore_databases(bundle, bundle)

    existing_destination = tmp_path / "already-restored"
    existing_destination.mkdir()
    sentinel = existing_destination / "keep.txt"
    sentinel.write_text("preserve existing data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_databases(bundle, existing_destination)
    assert sentinel.read_text(encoding="utf-8") == "preserve existing data"


def test_backup_requires_distinct_database_paths(tmp_path):
    same_path = tmp_path / "same.db"
    with sqlite3.connect(same_path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="distinct"):
        backup_databases(same_path, same_path, tmp_path / "backup")



def test_full_backup_and_restore_include_cloud_resource_database(initialized_databases, tmp_path):
    settings_path, learning_path, cloud_path = initialized_databases
    with sqlite3.connect(cloud_path) as connection:
        connection.execute(
            """INSERT INTO cloud_resource_candidates(
                   source_url, provider_domain, title, content_sha256, discovery_query
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                "https://provider.example/resources",
                "provider.example",
                "Example provider",
                "a" * 64,
                "test query",
            ),
        )

    bundle = tmp_path / "backups" / "full-snapshot"
    manifest = backup_databases(
        settings_path, learning_path, bundle, cloud_resources_path=cloud_path
    )
    assert manifest["format_version"] == 2
    assert set(manifest["databases"]) == {"settings", "learning", "cloud_resources"}

    restored = restore_databases(bundle, tmp_path / "restored-full")
    assert set(restored) == {"settings", "learning", "cloud_resources"}
    with sqlite3.connect(restored["cloud_resources"]) as connection:
        row = connection.execute(
            "SELECT source_url, title FROM cloud_resource_candidates"
        ).fetchone()
        assert row == ("https://provider.example/resources", "Example provider")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_configured_backup_includes_all_three_application_stores(initialized_databases, tmp_path):
    from app.db_backup import backup_configured_databases

    settings_path, learning_path, cloud_path = initialized_databases
    bundle = tmp_path / "backups" / "configured"
    manifest = backup_configured_databases(bundle)
    assert set(manifest["databases"]) == {"settings", "learning", "cloud_resources"}
    assert manifest["databases"]["settings"]["filename"] == "settings.sqlite3"
    assert manifest["databases"]["learning"]["filename"] == "learning.sqlite3"
    assert manifest["databases"]["cloud_resources"]["filename"] == "cloud_resources.sqlite3"


def test_restore_rejects_destination_inside_source_backup(initialized_databases, tmp_path):
    settings_path, learning_path, _cloud_path = initialized_databases
    bundle = tmp_path / "backup"
    backup_databases(settings_path, learning_path, bundle)

    with pytest.raises(ValueError, match="outside the backup directory"):
        restore_databases(bundle, bundle / "restored")

    assert sorted(path.name for path in bundle.iterdir()) == [
        "learning.sqlite3", "manifest.json", "settings.sqlite3"
    ]
