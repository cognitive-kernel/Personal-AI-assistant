import pytest

from app.learning_rubric import score_with_rubric, validate_rubric


def _rubric():
    return [
        {"criteria": [
            {"id": "definition", "weight": 2, "accepted_phrases": ["index improves lookup", "faster lookup"]},
            {"id": "tradeoff", "weight": 1, "accepted_phrases": ["maintenance cost", "cost of maintaining"]},
        ]},
        {"criteria": [
            {"id": "cause", "weight": 1, "accepted_phrases": ["transaction isolation", "isolation level"]},
            {"id": "effect", "weight": 1, "accepted_phrases": ["dirty read", "uncommitted data"]},
        ]},
    ]


def test_rubric_scoring_is_deterministic_and_partial():
    result = score_with_rubric(
        ["Faster lookup; maintenance cost.", "Isolation level prevents a dirty read."],
        _rubric(),
    )
    assert result["score"] == 100.0
    assert result["correct_count"] == 2
    assert result["scoring_method"] == "rubric_concept_coverage_v1"
    assert result["independent_verification"] is False


def test_rubric_scoring_gives_partial_credit_for_missing_concepts():
    result = score_with_rubric(
        ["Faster lookup.", "Isolation level."],
        _rubric(),
    )
    assert result["score"] == 60.0
    assert result["per_question"][0]["score"] == 66.67
    assert result["per_question"][1]["score"] == 50.0


def test_rubric_rejects_bad_shapes_and_unbounded_variants():
    assert validate_rubric([{"criteria": []}], 1) is False
    bad = _rubric()
    bad[0]["criteria"][0]["accepted_phrases"] = ["x"]
    assert validate_rubric(bad, 2) is False
    with pytest.raises(ValueError):
        score_with_rubric(["answer"], _rubric())


def test_normalization_handles_persian_arabic_character_variants():
    from app.learning_rubric import normalize_concept_text

    assert normalize_concept_text("يک كليد") == normalize_concept_text("یک کلید")


def test_learning_assessment_uses_rubric_and_hides_answer_cues_until_submission(tmp_path, monkeypatch):
    import json

    from app import db, learning
    from app.db import learning_connection

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    job = learning.create_job("Rubric scoring contract")
    assessment = {
        "questions": ["What improves lookup speed?", "Why does isolation matter?"],
        "answer_key": ["An index improves lookup speed but has maintenance cost.", "Isolation prevents dirty reads."],
        "rubric": _rubric(),
        "rubric_version": "concept-coverage-v1",
        "independently_verified": False,
    }
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )

    before = learning.get_job(job["id"])["assessment"]
    assert "rubric" not in before
    assert "answer_key" not in before

    attempt = learning.submit_assessment(
        job["id"],
        ["Faster lookup.", "Isolation level prevents a dirty read."],
    )
    assert attempt["scoring_method"] == "rubric_concept_coverage_v1"
    assert attempt["score"] == 80.0
    assert len(attempt["per_question"]) == 2
    assert attempt["independent_verification"] is False

    after = learning.get_job(job["id"])["assessment"]
    assert "rubric" in after
    assert "answer_key" in after


def test_learning_ui_explains_scientific_gate_is_not_independent_proof():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "learning.js").read_text(encoding="utf-8")
    assert "learning-scientific-status" in page
    assert "تأیید علمی مستقل محسوب نمی‌شود" in page
    assert "وضعیت علمی: تأیید نشده" in page


def test_rubric_does_not_credit_an_explicitly_negated_concept():
    result = score_with_rubric(
        ["not faster lookup; maintenance cost.", "no isolation level; no dirty read"],
        _rubric(),
    )
    assert result["per_question"][0]["criteria"][0]["matched"] is False
    assert result["per_question"][0]["criteria"][1]["matched"] is True
    assert result["per_question"][1]["criteria"][0]["matched"] is False

def test_rubric_score_updates_topic_progress_transactionally(tmp_path, monkeypatch):
    import json
    from app import db, learning
    from app.db import learning_connection

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("rubric-progress-test", "Rubric progress test"),
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Topic A", 1),
        ).lastrowid
    job = learning.create_job("Rubric progress", path_topic_id=topic_id)
    assessment = {
        "questions": ["Q1", "Q2"],
        "answer_key": ["faster lookup and maintenance cost", "isolation level and dirty read"],
        "rubric": _rubric(),
        "rubric_version": "concept-coverage-v1",
    }
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )

    result = learning.submit_assessment(
        job["id"],
        ["Faster lookup and maintenance cost.", "Isolation level prevents dirty read."],
    )
    topic = learning.get_learning_path(path_id)["topics"][0]
    assert result["score"] == 100.0
    assert topic["latest_score"] == 100.0
    assert topic["latest_score_kind"] == "learner_rubric"
    assert topic["status"] == "completed"


def test_rubric_below_threshold_cannot_be_manually_marked_complete(tmp_path, monkeypatch):
    import json
    import pytest
    from app import db, learning
    from app.db import learning_connection

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("rubric-threshold-test", "Rubric threshold test"),
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path_id, "Topic A", 1),
        ).lastrowid
    job = learning.create_job("Rubric threshold", path_topic_id=topic_id)
    assessment = {
        "questions": ["Q1", "Q2"],
        "answer_key": ["faster lookup and maintenance cost", "isolation level and dirty read"],
        "rubric": _rubric(),
        "rubric_version": "concept-coverage-v1",
    }
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )
    learning.submit_assessment(job["id"], ["Faster lookup.", "unrelated answer"])
    topic = learning.get_learning_path(path_id)["topics"][0]
    assert topic["status"] == "in_progress"
    assert topic["latest_score_kind"] == "learner_rubric"
    with pytest.raises(ValueError, match="only after a valid rubric assessment"):
        learning.update_path_topic(path_id, topic_id, "completed")


def test_assessment_idempotency_returns_existing_attempt_and_rejects_key_reuse(tmp_path, monkeypatch):
    import json
    import pytest
    from app import db, learning
    from app.db import learning_connection

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    job = learning.create_job("Idempotent assessment")
    assessment = {
        "questions": ["Q1", "Q2"],
        "answer_key": ["faster lookup and maintenance cost", "isolation level and dirty read"],
        "rubric": _rubric(),
        "rubric_version": "concept-coverage-v1",
    }
    with learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )
    answers = ["Faster lookup and maintenance cost.", "Isolation level and dirty read."]
    first = learning.submit_assessment(job["id"], answers, "assessment-key-0001")
    replay = learning.submit_assessment(job["id"], answers, "assessment-key-0001")
    assert replay["attempt_id"] == first["attempt_id"]
    assert replay["idempotent_replay"] is True
    with learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM assessment_attempts WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
    assert count == 1
    with pytest.raises(ValueError, match="already used with different answers"):
        learning.submit_assessment(
            job["id"], ["Different but valid answer.", "Isolation level and dirty read."],
            "assessment-key-0001",
        )


def test_assessment_idempotency_key_validation():
    import pytest
    from app.learning import submit_assessment

    with pytest.raises(ValueError, match="Idempotency key"):
        submit_assessment(1, ["answer"], "short")
