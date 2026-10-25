from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, learning_recommendations, spaced_repetition
from app.db import learning_connection
from app.main import app
from app.task_routes import require_loopback_client


def _setup(tmp_path, monkeypatch, topics):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("recommendation-test", "Recommendation test"),
        ).lastrowid
        topic_ids = []
        for position, item in enumerate(topics, start=1):
            topic_id = connection.execute(
                """INSERT INTO learning_path_topics(path_id, topic, position, status, latest_score)
                   VALUES (?, ?, ?, ?, ?)""",
                (path_id, item["topic"], position, item.get("status", "pending"), item.get("score")),
            ).lastrowid
            topic_ids.append(int(topic_id))
    return int(path_id), topic_ids


def test_recommendations_choose_only_the_next_sequential_topic(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [
        {"topic": "Basics", "status": "completed", "score": 85},
        {"topic": "Joins"},
        {"topic": "Window functions"},
    ])
    result = learning_recommendations.get_learning_recommendations(path_id=path_id)
    assert [item["topic_id"] for item in result["recommendations"]] == [topic_ids[1]]
    assert result["recommendations"][0]["action"] == "start_topic"
    assert result["recommendations"][0]["recommendation_type"] == "next_topic"
    assert result["autonomous_action_taken"] is False


def test_due_review_outranks_active_and_sequential_work(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [
        {"topic": "Review me", "status": "completed", "score": 92},
        {"topic": "Currently learning", "status": "in_progress"},
        {"topic": "Next topic"},
    ])
    base = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spaced_repetition, "_now", lambda: base)
    spaced_repetition.record_review(path_id, topic_ids[0], 4)

    result = learning_recommendations.get_learning_recommendations(
        path_id=path_id, now=base + timedelta(days=4)
    )
    assert result["recommendations"][0]["topic_id"] == topic_ids[0]
    assert result["recommendations"][0]["action"] == "review_topic"
    assert result["recommendations"][0]["overdue_days"] == 3
    assert result["recommendations"][1]["topic_id"] == topic_ids[1]
    assert all(item["topic_id"] != topic_ids[2] for item in result["recommendations"])


def test_low_score_completed_topic_is_reinforced_when_no_review_is_scheduled(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [
        {"topic": "Weak topic", "status": "completed", "score": 45},
        {"topic": "Next topic"},
    ])
    result = learning_recommendations.get_learning_recommendations(path_id=path_id)
    assert result["recommendations"][0]["topic_id"] == topic_ids[0]
    assert result["recommendations"][0]["action"] == "reinforce_topic"
    assert result["recommendations"][1]["topic_id"] == topic_ids[1]


def test_future_review_is_not_duplicated_as_reinforcement(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [
        {"topic": "Weak but scheduled", "status": "completed", "score": 50},
    ])
    base = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spaced_repetition, "_now", lambda: base)
    spaced_repetition.record_review(path_id, topic_ids[0], 4)
    result = learning_recommendations.get_learning_recommendations(
        path_id=path_id, now=base + timedelta(hours=1)
    )
    assert result["recommendations"] == []


def test_recommendations_validate_path_and_limit(tmp_path, monkeypatch):
    path_id, _ = _setup(tmp_path, monkeypatch, [{"topic": "Basics"}])
    with pytest.raises(KeyError):
        learning_recommendations.get_learning_recommendations(path_id=path_id + 999)
    with pytest.raises(ValueError):
        learning_recommendations.get_learning_recommendations(limit=0)
    with pytest.raises(ValueError):
        learning_recommendations.get_learning_recommendations(path_id=True)


def test_recommendations_endpoint_is_loopback_only_and_returns_deterministic_plan(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [{"topic": "Basics"}])
    app.dependency_overrides[require_loopback_client] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get("/api/learning/recommendations", params={"path_id": path_id})
    finally:
        app.dependency_overrides.pop(require_loopback_client, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["autonomous_action_taken"] is False
    assert body["recommendations"][0]["topic_id"] == topic_ids[0]


def test_learning_ui_exposes_recommendation_panel():
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "static"
    index = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "learning.js").read_text(encoding="utf-8")
    assert 'id="refresh-learning-recommendations"' in index
    assert 'id="learning-recommendations-output"' in index
    assert '"/api/learning/recommendations?limit=20"' in script
    assert "هیچ درس یا وظیفه‌ای به‌صورت خودکار شروع نشده است" in script


def test_in_progress_low_assessment_score_is_detected_as_knowledge_gap(tmp_path, monkeypatch):
    path_id, topic_ids = _setup(tmp_path, monkeypatch, [
        {"topic": "Weak active topic", "status": "in_progress", "score": 41.5},
        {"topic": "Next topic"},
    ])
    result = learning_recommendations.get_learning_recommendations(path_id=path_id)
    recommendation = result["recommendations"][0]
    assert recommendation["topic_id"] == topic_ids[0]
    assert recommendation["recommendation_type"] == "knowledge_gap"
    assert recommendation["action"] == "reinforce_topic"
    assert recommendation["gap_score"] == 38.5
    assert recommendation["priority"] > 85
    assert all(item["topic_id"] != topic_ids[1] for item in result["recommendations"])
