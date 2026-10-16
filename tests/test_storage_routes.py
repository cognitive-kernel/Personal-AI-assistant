from fastapi.testclient import TestClient

from app import cloud_resources, db
from app.main import app


def _isolate_app(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(db.defaults, "database_backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )


def test_backup_api_requires_confirmation_and_creates_complete_verified_bundle(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        rejected = client.post("/api/storage/backups", json={"confirm": False})
        assert rejected.status_code == 422

        created = client.post("/api/storage/backups", json={"confirm": True})
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "created"
        assert body["verified"] is True
        assert body["complete"] is True
        assert body["database_roles"] == ["cloud_resources", "learning", "settings"]
        assert str(tmp_path) not in created.text

        backup_dir = tmp_path / "backups" / body["id"]
        assert (backup_dir / "manifest.json").is_file()
        assert (backup_dir / "settings.sqlite3").is_file()
        assert (backup_dir / "learning.sqlite3").is_file()
        assert (backup_dir / "cloud_resources.sqlite3").is_file()

        listed = client.get("/api/storage/backups")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["items"][0]["id"] == body["id"]
        assert listed.json()["items"][0]["complete"] is True


def test_backup_api_is_loopback_only(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/storage/backups")
        assert response.status_code == 403
        assert not (tmp_path / "backups").exists()
