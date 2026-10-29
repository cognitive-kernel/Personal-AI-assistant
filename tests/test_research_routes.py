from fastapi.testclient import TestClient

from app import chat_history, cloud_resources, db, research_routes, task_manager
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


def test_chat_conversation_delete_api_removes_messages_and_returns_404_afterward(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/conversations")
        conversation_id = created.json()["conversation"]["id"]
        listed = client.get(f"/api/conversations/{conversation_id}/messages")
        assert listed.status_code == 200

        deleted = client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "conversation_id": conversation_id}
        assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
        assert client.delete(f"/api/conversations/{conversation_id}").status_code == 404


def test_research_route_rejects_non_loopback_clients(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.post("/api/research/tasks", json={"message": "debug this"})
    assert response.status_code == 403


def test_chat_conversation_api_keeps_old_conversations_and_persists_user_turn(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    captured = {}

    def fake_create_task(task_type, payload, **kwargs):
        captured.update(task_type=task_type, payload=payload, options=kwargs)
        return {"id": 321, "status": "queued", "task_type": task_type}

    monkeypatch.setattr(task_manager, "create_task", fake_create_task)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        first = client.post("/api/conversations")
        assert first.status_code == 201
        first_id = first.json()["conversation"]["id"]
        queued = client.post(
            "/api/research/tasks",
            json={"message": "سلام", "conversation_id": first_id},
        )
        assert queued.status_code == 202
        assert queued.json()["conversation_id"] == first_id
        assert captured["payload"]["conversation_id"] == first_id

        second = client.post("/api/conversations")
        assert second.status_code == 201
        second_id = second.json()["conversation"]["id"]
        assert second_id != first_id

        history = client.get(f"/api/conversations/{first_id}/messages")
        assert history.status_code == 200
        assert [(item["role"], item["content"]) for item in history.json()["messages"]] == [
            ("user", "سلام")
        ]
        conversations = client.get("/api/conversations").json()["conversations"]
        assert {item["id"] for item in conversations} >= {first_id, second_id}


def test_chat_message_feedback_api_validates_scope_and_persists_rating(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/conversations")
        conversation_id = created.json()["conversation"]["id"]
        user_message = chat_history.append_user_message(conversation_id, "question")
        assistant_message = chat_history.append_assistant_message(
            conversation_id, "answer", task_id=998
        )
        endpoint = (
            f"/api/conversations/{conversation_id}/messages/"
            f"{assistant_message['id']}/feedback"
        )

        saved = client.post(endpoint, json={"rating": -1})
        assert saved.status_code == 200
        assert saved.json()["feedback"]["rating"] == -1

        changed = client.post(endpoint, json={"rating": 1})
        assert changed.status_code == 200
        assert changed.json()["feedback"]["rating"] == 1

        history = client.get(f"/api/conversations/{conversation_id}/messages")
        assert history.status_code == 200
        assert history.json()["messages"][1]["feedback_rating"] == 1

        user_feedback = client.post(
            f"/api/conversations/{conversation_id}/messages/{user_message['id']}/feedback",
            json={"rating": 1},
        )
        assert user_feedback.status_code == 422
        invalid_rating = client.post(endpoint, json={"rating": 0})
        assert invalid_rating.status_code == 422
        boolean_rating = client.post(endpoint, json={"rating": True})
        assert boolean_rating.status_code == 422
        string_rating = client.post(endpoint, json={"rating": "1"})
        assert string_rating.status_code == 422
        missing = client.post(
            f"/api/conversations/{conversation_id}/messages/999999/feedback",
            json={"rating": 1},
        )
        assert missing.status_code == 404
