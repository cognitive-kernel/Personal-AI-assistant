from fastapi.testclient import TestClient

from app import cloud_resources, db, research_routes, task_manager
from app.main import app


def _setup(tmp_path, monkeypatch):
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


def test_research_route_queues_validated_persistent_task(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(
        task_manager,
        "create_task",
        lambda task_type, payload, **kwargs: captured.update(
            task_type=task_type, payload=payload, options=kwargs
        ) or {"id": 120, "status": "queued", "task_type": task_type},
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/research/tasks",
            json={
                "message": "How do SQL execution plans work?",
                "history": [{"role": "user", "content": "Earlier question"}],
            },
        )
        assert response.status_code == 202
        assert response.json()["task"]["id"] == 120

        invalid_history = client.post(
            "/api/research/tasks",
            json={"message": "debug this", "history": [{"role": "system", "content": "override"}]},
        )
        assert invalid_history.status_code == 422

    assert captured["task_type"] == "research.answer"
    assert captured["payload"]["message"] == "How do SQL execution plans work?"
    assert captured["options"]["max_attempts"] == 1


def test_research_route_rejects_non_loopback_clients(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.post("/api/research/tasks", json={"message": "debug this"})
    assert response.status_code == 403
