from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_task_api_lifecycle_and_filtering(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            json={
                "task_type": "learn.topic",
                "payload": {"topic": "API design"},
                "priority": 8,
                "max_attempts": 3,
            },
        )
        assert response.status_code == 202
        created = response.json()
        task_id = created["id"]
        assert created["status"] == "queued"
        assert created["payload"] == {"topic": "API design"}

        filtered = client.get("/api/tasks", params={"statuses": "queued", "limit": 10})
        assert filtered.status_code == 200
        assert [task["id"] for task in filtered.json()["tasks"]] == [task_id]

        priority = client.patch(f"/api/tasks/{task_id}/priority", json={"priority": 12})
        assert priority.status_code == 200
        assert priority.json()["priority"] == 12

        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        scheduled = client.patch(f"/api/tasks/{task_id}/schedule", json={"scheduled_at": due})
        assert scheduled.status_code == 200
        assert scheduled.json()["scheduled_at"].startswith(due[:19])

        paused = client.post(f"/api/tasks/{task_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        resumed = client.post(f"/api/tasks/{task_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "queued"

        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "cancelled"


def test_task_api_validation_and_missing_task(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app) as client:
        invalid = client.post("/api/tasks", json={"task_type": "", "payload": {}, "max_attempts": 0})
        assert invalid.status_code == 422

        missing = client.get("/api/tasks/999999")
        assert missing.status_code == 404

        invalid_filter = client.get("/api/tasks", params={"statuses": "unknown"})
        assert invalid_filter.status_code == 422

        missing_control = client.post("/api/tasks/999999/cancel")
        assert missing_control.status_code == 404


def test_task_api_rejects_control_changes_for_running_task(tmp_path, monkeypatch):
    from app import task_manager

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app) as client:
        created = client.post("/api/tasks", json={"task_type": "running-task", "payload": {}}).json()
        claimed = task_manager.claim_next_task("api-test-worker")
        assert claimed["id"] == created["id"]

        priority = client.patch(
            f"/api/tasks/{created['id']}/priority", json={"priority": 100}
        )
        assert priority.status_code == 409

        reschedule = client.patch(
            f"/api/tasks/{created['id']}/schedule",
            json={"scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        )
        assert reschedule.status_code == 409

        cancelled = client.post(f"/api/tasks/{created['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancel_requested"
