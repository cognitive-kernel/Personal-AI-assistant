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



def test_dashboard_requires_visible_confirmation_for_full_backup():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="create-storage-backup"' in page
    assert 'id="refresh-storage-backups"' in page
    assert 'api("/api/storage/backups",{method:"POST",body:JSON.stringify({confirm:true})})' in page
    assert "window.confirm(" in page



def test_restore_api_stages_verified_backup_without_changing_live_databases(tmp_path, monkeypatch):
    import hashlib
    import sqlite3

    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/storage/backups", json={"confirm": True})
        assert created.status_code == 201
        backup_id = created.json()["id"]

        live_paths = [
            tmp_path / "settings.db",
            tmp_path / "learning.db",
            tmp_path / "cloud_resources.db",
        ]

        def digests():
            return {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in live_paths
            }

        before = digests()
        rejected = client.post(
            f"/api/storage/backups/{backup_id}/restore", json={"confirm": False}
        )
        assert rejected.status_code == 422

        restored = client.post(
            f"/api/storage/backups/{backup_id}/restore", json={"confirm": True}
        )
        assert restored.status_code == 201
        body = restored.json()
        assert body["status"] == "staged"
        assert body["source_backup_id"] == backup_id
        assert body["database_roles"] == ["cloud_resources", "learning", "settings"]
        assert body["verified"] is True
        assert body["live_databases_changed"] is False
        assert str(tmp_path) not in restored.text
        staging_dir = tmp_path / "backups" / "restore-staging" / body["restore_id"]
        assert (staging_dir / "settings.sqlite3").is_file()
        assert (staging_dir / "learning.sqlite3").is_file()
        assert (staging_dir / "cloud_resources.sqlite3").is_file()
        for name in ("settings.sqlite3", "learning.sqlite3", "cloud_resources.sqlite3"):
            with sqlite3.connect(staging_dir / name) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert digests() == before


def test_restore_api_rejects_unknown_or_malformed_backup_ids(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        for backup_id in ("../settings.db", "snapshot-20260101T000000000000Z", "missing"):
            response = client.post(
                f"/api/storage/backups/{backup_id}/restore", json={"confirm": True}
            )
            assert response.status_code == 404
        assert not (tmp_path / "backups" / "restore-staging").exists()


def test_restore_api_rejects_tampered_backup(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/storage/backups", json={"confirm": True})
        assert created.status_code == 201
        backup_id = created.json()["id"]
        backup_db = tmp_path / "backups" / backup_id / "settings.sqlite3"
        with backup_db.open("ab") as stream:
            stream.write(b"tampered")
        response = client.post(
            f"/api/storage/backups/{backup_id}/restore", json={"confirm": True}
        )
        assert response.status_code == 422
        assert not (tmp_path / "backups" / "restore-staging").exists()



def test_dashboard_exposes_explicit_staging_restore_without_live_cutover():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "بازیابی آزمایشی در پوشهٔ جدا" in page
    assert "دیتابیس‌های زنده تغییر نمی‌کنند" in page
    assert '"/api/storage/backups/"+encodeURIComponent(backupId)+"/restore"' in page



def test_backup_prune_previews_then_deletes_only_old_backups_beyond_minimum_retention(tmp_path, monkeypatch):
    import os
    import time

    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        backup_ids = []
        for _ in range(5):
            response = client.post("/api/storage/backups", json={"confirm": True})
            assert response.status_code == 201
            backup_ids.append(response.json()["id"])

        root = tmp_path / "backups"
        old_time = time.time() - 60 * 86400
        for backup_id in backup_ids[:2]:
            path = root / backup_id
            os.utime(path, (old_time, old_time))

        preview = client.post(
            "/api/storage/backups/prune",
            json={"confirm": False, "keep_last": 3, "min_age_days": 30},
        )
        assert preview.status_code == 200
        preview_body = preview.json()
        assert preview_body["status"] == "preview"
        assert preview_body["would_delete_count"] == 2
        assert {item["id"] for item in preview_body["candidates"]} == set(backup_ids[:2])
        assert all((root / backup_id).is_dir() for backup_id in backup_ids)

        pruned = client.post(
            "/api/storage/backups/prune",
            json={"confirm": True, "keep_last": 3, "min_age_days": 30},
        )
        assert pruned.status_code == 200
        body = pruned.json()
        assert body["status"] == "completed"
        assert body["deleted_count"] == 2
        assert set(body["deleted"]) == set(backup_ids[:2])
        assert all(not (root / backup_id).exists() for backup_id in backup_ids[:2])
        assert all((root / backup_id).is_dir() for backup_id in backup_ids[2:])
        assert not (root / "restore-staging").exists()


def test_backup_prune_enforces_conservative_minimums_and_loopback_access(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.post(
            "/api/storage/backups/prune",
            json={"confirm": True, "keep_last": 2, "min_age_days": 30},
        ).status_code == 422
        assert client.post(
            "/api/storage/backups/prune",
            json={"confirm": True, "keep_last": 3, "min_age_days": 1},
        ).status_code == 422
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.post(
            "/api/storage/backups/prune",
            json={"confirm": True, "keep_last": 3, "min_age_days": 30},
        )
        assert response.status_code == 403



def test_dashboard_exposes_preview_first_backup_retention_controls():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="prune-storage-backups"' in page
    assert '"/api/storage/backups/prune"' in page
    assert "keep_last:3,min_age_days:30" in page
    assert "این کار قابل بازگشت نیست." in page
