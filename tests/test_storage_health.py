import sqlite3

from app import cloud_resources, db, main, storage_health


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )


def test_storage_health_reports_all_versioned_stores_healthy_without_paths(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    db.init_databases()
    cloud_resources.initialize_store()

    result = storage_health.check_storage_health()

    assert result["status"] == "ok"
    assert result["read_only"] is True
    assert result["repair_performed"] is False
    assert {item["name"] for item in result["databases"]} == {
        "settings", "learning", "cloud_resources"
    }
    assert all(item["status"] == "ok" for item in result["databases"])
    assert all(item["integrity"] == "ok" for item in result["databases"])
    assert all(item["migration_history_valid"] for item in result["databases"])
    assert all(item["schema_fingerprint_matches"] for item in result["databases"])
    assert str(tmp_path) not in str(result)


def test_storage_health_is_read_only_and_does_not_create_missing_files(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    result = storage_health.check_storage_health()

    assert result["status"] == "degraded"
    assert all(item["status"] == "missing" for item in result["databases"])
    assert not (tmp_path / "settings.db").exists()
    assert not (tmp_path / "learning.db").exists()
    assert not (tmp_path / "cloud_resources.db").exists()


def test_storage_health_detects_schema_drift_without_repairing_it(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    db.init_databases()
    cloud_resources.initialize_store()
    with sqlite3.connect(db.defaults.learning_db_path) as connection:
        connection.execute("CREATE TABLE untracked_health_drift (id INTEGER PRIMARY KEY)")

    result = storage_health.check_storage_health()
    learning = next(item for item in result["databases"] if item["name"] == "learning")

    assert result["status"] == "degraded"
    assert learning["status"] == "schema_drift"
    assert learning["integrity"] == "ok"
    with sqlite3.connect(db.defaults.learning_db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='untracked_health_drift'"
        ).fetchone() is not None



def test_general_health_does_not_disclose_database_filesystem_paths(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    db.init_databases()
    monkeypatch.setattr(main, "detect_hardware", lambda: {"cpu_count": 2})
    monkeypatch.setattr(main, "runtime_limits", lambda settings, hardware: {"workers": 1})
    monkeypatch.setattr(main.ollama, "health", lambda: {"status": "ok"})

    result = main.api_health()

    assert result["storage"]["separate"] is True
    assert str(tmp_path) not in str(result)
    assert db.defaults.settings_db_path not in str(result)
    assert db.defaults.learning_db_path not in str(result)
