import time

from fastapi.testclient import TestClient

from app import cloud_resources, db
from app.main import app


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )


def test_cloud_resource_api_lists_candidates_and_latest_run(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/cloud-resources", params={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {"latest_run": None, "candidates": []}


def test_cloud_resource_api_queues_manual_discovery_without_executing_inline(
    tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    def fake_discover(*, checkpoint=None):
        return {
            "run_id": 1,
            "status": "completed",
            "queries_attempted": 3,
            "sources_seen": 1,
            "candidates_upserted": 1,
            "error_summary": None,
        }
    monkeypatch.setattr(cloud_resources, "discover_resources", fake_discover)

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post("/api/cloud-resources/discover")

    assert response.status_code == 202
    body = response.json()
    assert body["task"]["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert body["task"]["payload"] == {"trigger": "manual"}
    assert "remain unreviewed" in body["message"]


def test_cloud_resource_api_rejects_non_loopback_clients(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/cloud-resources")

    assert response.status_code == 403
