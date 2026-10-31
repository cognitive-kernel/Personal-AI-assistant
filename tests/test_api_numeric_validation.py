import json

import pytest
from fastapi.testclient import TestClient

from app import db, learning, main, task_manager


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    db.init_databases()
    monkeypatch.setattr(main, "_startup_storage_error", None)
    return TestClient(main.app, client=("127.0.0.1", 12345))


def test_browser_learning_api_rejects_boolean_lesson_count(api_client, monkeypatch):
    monkeypatch.setattr(main.browser_learning, "dispatch_next_course", lambda: None)

    response = api_client.post(
        "/api/learning/browser/courses",
        json={"topic": "Strict lesson count", "max_lessons": True},
    )

    assert response.status_code == 422
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM browser_learning_courses"
        ).fetchone()[0]
    assert count == 0


def test_lesson_review_api_rejects_boolean_rating_without_mutation(api_client):
    job = learning.create_job("Strict review rating")
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps({"learning_outcomes": ["one"]}), job["id"]),
        )

    response = api_client.post(
        f"/api/learning/jobs/{job['id']}/review",
        json={"outcomes": ["mastered"], "value_rating": True},
    )

    assert response.status_code == 422
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM lesson_reviews WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
    assert count == 0


def test_learning_path_api_rejects_boolean_score_without_mutation(api_client):
    with db.learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("strict-score-test", "Strict score test"),
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Strict score topic", 1),
        ).lastrowid

    response = api_client.patch(
        f"/api/learning/paths/{path_id}/topics/{topic_id}",
        json={"status": "in_progress", "latest_score": True},
    )

    assert response.status_code == 422
    with db.learning_connection() as connection:
        score = connection.execute(
            "SELECT latest_score FROM learning_path_topics WHERE id=?", (topic_id,)
        ).fetchone()["latest_score"]
    assert score is None


@pytest.mark.parametrize("score", [82, 82.5])
def test_learning_path_api_preserves_valid_integer_and_float_scores(
    api_client, score
):
    with db.learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("valid-score-test", "Valid score test"),
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Valid score topic", 1),
        ).lastrowid

    response = api_client.patch(
        f"/api/learning/paths/{path_id}/topics/{topic_id}",
        json={"status": "in_progress", "latest_score": score},
    )

    assert response.status_code == 200
    assert float(response.json()["topics"][0]["latest_score"]) == score


def test_browser_learning_api_accepts_integer_lesson_count(api_client, monkeypatch):
    monkeypatch.setattr(main.browser_learning, "dispatch_next_course", lambda: None)

    response = api_client.post(
        "/api/learning/browser/courses",
        json={"topic": "Valid lesson count", "max_lessons": 2},
    )

    assert response.status_code == 200
    assert response.json()["max_lessons"] == 2


@pytest.mark.parametrize("field", ["priority", "max_attempts"])
def test_task_create_api_rejects_boolean_integer_fields_atomically(
    api_client, field
):
    response = api_client.post(
        "/api/tasks",
        json={
            "task_type": "api.numeric-validation",
            "payload": {},
            field: True,
        },
    )

    assert response.status_code == 422
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM task_queue WHERE task_type=?",
            ("api.numeric-validation",),
        ).fetchone()[0]
    assert count == 0


def test_task_priority_api_rejects_boolean_without_mutation(api_client):
    task = task_manager.create_task("api.numeric-validation", {})

    response = api_client.patch(
        f"/api/tasks/{task['id']}/priority",
        json={"priority": True},
    )

    assert response.status_code == 422
    assert task_manager.get_task(task["id"])["priority"] == 0


def test_research_api_rejects_boolean_conversation_id_without_mutation(api_client):
    conversation = main.chat_history.create_conversation()

    response = api_client.post(
        "/api/research/tasks",
        json={"message": "Find a primary source", "conversation_id": True},
    )

    assert response.status_code == 422
    assert main.chat_history.list_messages(conversation["id"]) == []
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM task_queue WHERE task_type='research.answer'"
        ).fetchone()[0]
    assert count == 0


def test_research_plan_api_rejects_fractional_step_count_without_mutation(api_client):
    response = api_client.post(
        "/api/research/plans",
        json={"goal": "Review the API contract", "max_steps": 2.5},
    )

    assert response.status_code == 422
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM task_queue WHERE task_type='research.plan'"
        ).fetchone()[0]
    assert count == 0
