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



def test_research_plan_route_queues_bounded_plan_task(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    captured = {}

    def fake_create_task(task_type, payload, **kwargs):
        captured.update(task_type=task_type, payload=payload, options=kwargs)
        return {"id": 765, "status": "queued", "task_type": task_type}

    monkeypatch.setattr(task_manager, "create_task", fake_create_task)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/research/plans",
            json={"goal": "Compare SQL Server indexing strategies", "max_steps": 3},
        )
        assert response.status_code == 202
        assert response.json()["task_id"] == 765
        invalid = client.post(
            "/api/research/plans",
            json={"goal": "goal", "max_steps": 9},
        )
        assert invalid.status_code == 422

    assert captured["task_type"] == "research.plan"
    assert captured["payload"] == {
        "goal": "Compare SQL Server indexing strategies",
        "max_steps": 3,
    }
    assert captured["options"]["max_attempts"] == 1


def test_research_plan_progress_endpoint_returns_checkpoint_progress(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan = [
        {
            "question": "What is documented?",
            "source_focus": "Official docs",
            "acceptance_criteria": "Use primary sources",
        },
        {
            "question": "What are the limitations?",
            "source_focus": "Official release notes",
            "acceptance_criteria": "List caveats",
        },
    ]
    monkeypatch.setattr(
        task_manager,
        "get_task",
        lambda task_id: {
            "id": task_id,
            "task_type": "research.plan",
            "status": "paused",
            "error": None,
            "result": None,
            "checkpoint": {
                "phase": "step_completed",
                "plan": plan,
                "completed_results": [
                    {"step_index": 0, "question": plan[0]["question"], "answer": "A sourced answer"}
                ],
                "next_step_index": 1,
                "total_steps": 2,
            },
        },
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/research/plans/44")
        assert response.status_code == 200
        assert response.json()["completed_steps"] == 1
        assert response.json()["total_steps"] == 2
        assert response.json()["results"][0]["answer"] == "A sourced answer"
        assert client.get("/api/research/plans/45").status_code == 200

    monkeypatch.setattr(task_manager, "get_task", lambda _task_id: None)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/research/plans/46").status_code == 404
