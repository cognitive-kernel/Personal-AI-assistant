import pytest

from app import db, learning
from app.db import learning_connection


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("prerequisite-test", "Prerequisite test"),
        ).lastrowid
        first_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Foundations", 1),
        ).lastrowid
        second_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Advanced topic", 2),
        ).lastrowid
    return path_id, first_id, second_id


def test_later_topic_is_locked_until_all_prior_topics_are_completed(tmp_path, monkeypatch):
    path_id, first_id, second_id = _setup(tmp_path, monkeypatch)
    path = learning.get_learning_path(path_id)
    first, second = path["topics"]
    assert first["can_start"] is True
    assert second["can_start"] is False
    assert second["blocked_by"] == [{"id": first_id, "topic": "Foundations"}]

    with pytest.raises(ValueError, match="Complete prerequisite topics"):
        learning.start_topic_learning(path_id, second_id)

    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_path_topics SET status='completed', latest_score=100, latest_score_kind='learner_rubric' WHERE id=?",
            (first_id,),
        )
    path = learning.get_learning_path(path_id)
    assert path["topics"][1]["can_start"] is True
    assert path["topics"][1]["blocked_by"] == []


def test_regeneration_cannot_bypass_prerequisite_locks(tmp_path, monkeypatch):
    path_id, _first_id, second_id = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="before regenerating"):
        learning.regenerate_topic_learning(path_id, second_id)


def test_assessment_endpoint_cannot_complete_a_locked_topic(tmp_path, monkeypatch):
    import json

    path_id, first_id, second_id = _setup(tmp_path, monkeypatch)
    # Create a pre-existing job while prerequisites are satisfied, then simulate
    # a prerequisite being reset before a stale job's assessment is submitted.
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_path_topics SET status='completed', latest_score=100, latest_score_kind='learner_rubric' WHERE id=?",
            (first_id,),
        )
    job = learning.create_job("Locked topic assessment", path_topic_id=second_id)
    with learning_connection() as connection:
        connection.execute("UPDATE learning_path_topics SET status='pending' WHERE id=?", (first_id,))
    assessment = {
        "questions": ["Q1", "Q2"],
        "answer_key": ["faster lookup", "dirty read"],
        "rubric": [
            {"criteria": [
                {"id": "a", "weight": 1, "accepted_phrases": ["faster lookup"]},
                {"id": "b", "weight": 1, "accepted_phrases": ["index cost"]},
            ]},
            {"criteria": [
                {"id": "c", "weight": 1, "accepted_phrases": ["isolation level"]},
                {"id": "d", "weight": 1, "accepted_phrases": ["dirty read"]},
            ]},
        ],
    }
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )
    with pytest.raises(ValueError, match="locked topic"):
        learning.submit_assessment(job["id"], ["faster lookup and index cost", "isolation level and dirty read"])
    with learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM assessment_attempts WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
    assert count == 0
