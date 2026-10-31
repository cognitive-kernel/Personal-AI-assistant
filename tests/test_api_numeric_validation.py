import json

import pytest
from fastapi.testclient import TestClient

from app import db, learning, main


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    db.init_databases()
    monkeypatch.setattr(main, "_startup_storage_error", None)
    return TestClient(main.app)


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
