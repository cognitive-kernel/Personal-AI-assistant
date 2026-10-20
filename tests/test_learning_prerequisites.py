import json

import pytest

from app import db, learning


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('ordered-test', 'Ordered test')"
        ).lastrowid)
        topic_a = int(connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Foundations', 1)",
            (path_id,),
        ).lastrowid)
        topic_b = int(connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, 'Advanced topic', 2)",
            (path_id,),
        ).lastrowid)
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda _job_id: {})
    return path_id, topic_a, topic_b


def _assessment():
    rubric = [
        {"required_concepts": [
            {"concept": "alpha", "accepted_phrases": ["alpha"]},
            {"concept": "beta", "accepted_phrases": ["beta"]},
        ]}
        for _ in range(3)
    ]
    return {
        "questions": ["Q1", "Q2", "Q3"],
        "answer_key": ["alpha beta"] * 3,
        "answer_rubric": rubric,
    }


def test_prerequisites_block_advanced_topics_until_rubric_mastery(tmp_path, monkeypatch):
    path_id, topic_a, topic_b = _setup(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Prerequisite topics must be completed first"):
        learning.start_topic_learning(path_id, topic_b)

    path = learning.get_learning_path(path_id)
    assert path["topics"][1]["prerequisites_met"] is False
    assert path["topics"][1]["blocked_by"] == [{"id": topic_a, "topic": "Foundations"}]

    queued = learning.start_path_learning(path_id)
    assert queued["queued_count"] == 1
    assert queued["skipped_prerequisite_count"] == 1
    first_job_id = queued["job_ids"][0]

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(_assessment()), first_job_id),
        )
    outcome = learning.submit_assessment(first_job_id, ["alpha beta"] * 3)
    assert outcome["mastery_achieved"] is True

    path = learning.get_learning_path(path_id)
    assert path["topics"][0]["status"] == "completed"
    assert path["topics"][1]["prerequisites_met"] is True
    next_job = learning.start_topic_learning(path_id, topic_b)
    assert next_job["path_topic_id"] == topic_b


def test_manual_completion_cannot_bypass_prerequisites(tmp_path, monkeypatch):
    path_id, _topic_a, topic_b = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Prerequisite topics must be completed"):
        learning.update_path_topic(path_id, topic_b, "completed", 100)
