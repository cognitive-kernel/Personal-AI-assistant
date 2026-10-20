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
