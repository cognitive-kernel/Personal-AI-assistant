import json

import pytest

from app import db, learning, learning_practical


def _init_learning_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _create_path(*, topics):
    with db.learning_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("test-learning-path", "Test learning path"),
        )
        path_id = int(cursor.lastrowid)
        connection.execute("UPDATE learning_paths SET prerequisite_mode='linear' WHERE id=?", (path_id,))
        topic_ids = []
        for position, topic in enumerate(topics, start=1):
            cursor = connection.execute(
                "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
                (path_id, topic, position),
            )
            topic_ids.append(int(cursor.lastrowid))
    return path_id, topic_ids


def _assessment():
    return {
        "questions": ["Q1", "Q2", "Q3"],
        "answer_key": ["A1", "A2", "A3"],
        "learning_outcomes": ["one", "two", "three"],
    }


def test_learning_path_blocks_topic_until_previous_topic_is_completed(tmp_path, monkeypatch):
    _init_learning_db(tmp_path, monkeypatch)
    _path_id, topic_ids = _create_path(topics=["First topic", "Second topic"])

    first = learning.create_job("First topic", path_topic_id=topic_ids[0])
    assert first["path_topic_id"] == topic_ids[0]

    with pytest.raises(ValueError, match="Prerequisite topic is not completed: First topic"):
        learning.create_job("Second topic", path_topic_id=topic_ids[1])

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_path_topics SET status='completed' WHERE id=?",
            (topic_ids[0],),
        )
    second = learning.create_job("Second topic", path_topic_id=topic_ids[1])
    assert second["path_topic_id"] == topic_ids[1]


def test_passing_assessment_completes_topic_and_updates_progress_score(tmp_path, monkeypatch):
    _init_learning_db(tmp_path, monkeypatch)
    _path_id, topic_ids = _create_path(topics=["Assessment topic"])
    job = learning.create_job("Assessment topic", path_topic_id=topic_ids[0])
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(_assessment()), job["id"]),
        )

    result = learning.submit_assessment(job["id"], ["A1", "A2", "A3"])
    assert result["score"] == 100
    assert result["path_topic_status"] == "completed"
    with db.learning_connection() as connection:
        topic = connection.execute(
            "SELECT status, latest_score FROM learning_path_topics WHERE id=?",
            (topic_ids[0],),
        ).fetchone()
    assert topic["status"] == "completed"
    assert topic["latest_score"] == 100


def test_below_threshold_assessment_keeps_topic_in_progress(tmp_path, monkeypatch):
    _init_learning_db(tmp_path, monkeypatch)
    _path_id, topic_ids = _create_path(topics=["Assessment topic"])
    job = learning.create_job("Assessment topic", path_topic_id=topic_ids[0])
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(_assessment()), job["id"]),
        )

    result = learning.submit_assessment(job["id"], ["A1", "wrong", "wrong"])
    assert result["score"] == pytest.approx(33.33)
    assert result["path_topic_status"] == "in_progress"
    with db.learning_connection() as connection:
        topic = connection.execute(
            "SELECT status, latest_score FROM learning_path_topics WHERE id=?",
            (topic_ids[0],),
        ).fetchone()
    assert topic["status"] == "in_progress"
    assert topic["latest_score"] == pytest.approx(33.33)


def _create_practical_assessment(tmp_path, monkeypatch, *, passing_cases):
    _init_learning_db(tmp_path, monkeypatch)
    path_id, topic_ids = _create_path(topics=["Python lists"])
    job = learning.create_job("Python lists", path_topic_id=topic_ids[0])
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed' WHERE id=?",
            (job["id"],),
        )
    cases = learning_practical.get_test_cases("python-json-sum-v1")
    results = [
        {"case_id": case["id"], "passed": index < passing_cases}
        for index, case in enumerate(cases)
    ]
    report = learning_practical.build_grade_report("python-json-sum-v1", results)
    return path_id, topic_ids[0], job["id"], report


def test_practical_assessment_persists_provenance_and_uses_shared_completion_gate(tmp_path, monkeypatch):
    path_id, topic_id, job_id, report = _create_practical_assessment(
        tmp_path, monkeypatch, passing_cases=3
    )

    result = learning_practical.record_practical_attempt(
        job_id, "python-json-sum-v1", report, "print(1 + 2)"
    )
    assert result["score"] == 75.0
    assert result["mastery_achieved"] is True

    with db.learning_connection() as connection:
        topic = connection.execute(
            "SELECT status, latest_score, latest_score_source FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    assert topic["status"] == "completed"
    assert topic["latest_score"] == 75.0
    assert topic["latest_score_source"] == "sandbox_practical_v1"

    learning.update_path_topic(path_id, topic_id, "completed")
    with pytest.raises(ValueError, match="assessment-derived score cannot be manually overridden"):
        learning.update_path_topic(path_id, topic_id, "completed", 100)


def test_practical_assessment_below_threshold_cannot_be_manually_completed(tmp_path, monkeypatch):
    path_id, topic_id, job_id, report = _create_practical_assessment(
        tmp_path, monkeypatch, passing_cases=2
    )

    result = learning_practical.record_practical_attempt(
        job_id, "python-json-sum-v1", report, "print(1 + 2)"
    )
    assert result["score"] == 50.0
    assert result["mastery_achieved"] is False

    with db.learning_connection() as connection:
        topic = connection.execute(
            "SELECT status, latest_score, latest_score_source FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    assert topic["status"] == "in_progress"
    assert topic["latest_score"] == 50.0
    assert topic["latest_score_source"] == "sandbox_practical_v1"

    with pytest.raises(ValueError, match="at least 70/100"):
        learning.update_path_topic(path_id, topic_id, "completed")
