from fastapi.testclient import TestClient

from app import db, factual_source_checks
from app.main import app


def test_factual_source_freshness_routes_require_loopback_and_confirmation(tmp_path, monkeypatch):
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
        factual_source_checks,
        "list_refresh_queue",
        lambda limit=50: [{"id": 7, "lifecycle_status": "active", "evidence": []}],
    )
    monkeypatch.setattr(
        factual_source_checks,
        "refresh_claim_sources",
        lambda knowledge_id, confirm: {
            "knowledge_id": knowledge_id,
            "checks": [{"outcome": "baseline"}],
            "needs_review": False,
            "verified": False,
        },
    )
    monkeypatch.setattr(
        factual_source_checks,
        "list_source_checks",
        lambda knowledge_id, limit=50: [{"id": 1, "knowledge_id": knowledge_id, "outcome": "baseline"}],
    )

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        queue = client.get("/api/factual-knowledge/source-refresh-queue")
        assert queue.status_code == 200
        assert queue.json()["count"] == 1

        denied = client.post(
            "/api/factual-knowledge/7/refresh-sources",
            json={"confirm": False},
        )
        assert denied.status_code == 422

        refreshed = client.post(
            "/api/factual-knowledge/7/refresh-sources",
            json={"confirm": True},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["verified"] is False

        history = client.get("/api/factual-knowledge/7/source-checks")
        assert history.status_code == 200
        assert history.json()["items"][0]["outcome"] == "baseline"
