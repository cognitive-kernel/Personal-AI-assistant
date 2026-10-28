from fastapi.testclient import TestClient

from app import db, knowledge_base
from app.main import app


def test_source_index_routes_are_registered_loopback_only_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        "app.cloud_resources.ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )
    monkeypatch.setattr(
        knowledge_base,
        "ingest_url",
        lambda url, title=None: {
            "source_id": 1, "url": url, "title": title or "Docs",
            "chunks": 2, "unchanged": False, "status": "indexed",
        },
    )
    monkeypatch.setattr(knowledge_base, "list_sources", lambda: [{"id": 1, "url": "https://learn.microsoft.com/en-us/sql/"}])
    monkeypatch.setattr(
        knowledge_base, "search",
        lambda query, limit=5: [{"title": "Docs", "url": "https://learn.microsoft.com/en-us/sql/", "content": "Index excerpt", "retrieval": "sqlite_fts5"}],
    )

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        invalid = client.post(
            "/api/knowledge/sources",
            json={"url": "https://learn.microsoft.com/en-us/sql/", "extra": True},
        )
        assert invalid.status_code == 422

        added = client.post(
            "/api/knowledge/sources",
            json={"url": "https://learn.microsoft.com/en-us/sql/", "title": "SQL docs"},
        )
        assert added.status_code == 200
        assert added.json()["status"] == "indexed"

        listed = client.get("/api/knowledge/sources")
        assert listed.status_code == 200
        assert listed.json()["sources"][0]["id"] == 1

        searched = client.post(
            "/api/knowledge/sources/search",
            json={"query": "SQL Server", "limit": 3},
        )
        assert searched.status_code == 200
        assert searched.json()["results"][0]["retrieval"] == "sqlite_fts5"
