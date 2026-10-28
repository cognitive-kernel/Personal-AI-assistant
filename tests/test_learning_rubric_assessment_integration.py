import json

from app import db, learning


def test_rubric_assessment_is_deterministic_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Indexing and transaction isolation")
    rubric = [
        {"criteria": [
            {"id": "lookup", "weight": 2, "accepted_phrases": ["faster lookup", "improves lookup"]},
            {"id": "tradeoff", "weight": 1, "accepted_phrases": ["maintenance cost", "cost of maintenance"]},
        ]},
        {"criteria": [
            {"id": "isolation", "weight": 1, "accepted_phrases": ["transaction isolation", "isolation level"]},
            {"id": "dirty-read", "weight": 1, "accepted_phrases": ["dirty read", "uncommitted data"]},
        ]},
    ]
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', path_topic_id=NULL, assessment_json=? WHERE id=?",
            (json.dumps({
                "questions": ["Why use an index?", "What does transaction isolation prevent?"],
                "answer_key": ["An index improves lookup but adds maintenance cost.", "Isolation prevents dirty reads."],
                "rubric": rubric,
                "rubric_version": "concept-coverage-v1",
            }), job["id"]),
        )

    before = learning.get_job(job["id"])
    assert "rubric" not in before["assessment"]
    assert "rubric_version" not in before["assessment"]

    key = "assessment-test-key-0001"
    answers = ["Faster lookup; maintenance cost.", "Transaction isolation prevents a dirty read."]
    first = learning.submit_assessment(job["id"], answers, key)
    replay = learning.submit_assessment(job["id"], answers, key)
    assert first["score"] == 100.0
    assert first["scoring_method"] == "rubric_concept_coverage_v1"
    assert first["independent_verification"] is False
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["attempt_id"] == first["attempt_id"]
    assert replay["score"] == first["score"]

    try:
        learning.submit_assessment(job["id"], ["different answer", answers[1]], key)
    except ValueError as exc:
        assert "different answers" in str(exc)
    else:
        raise AssertionError("Reusing an idempotency key with different answers must fail")

    with db.learning_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM assessment_attempts WHERE job_id=? AND idempotency_key=?",
            (job["id"], key),
        ).fetchall()
    assert len(rows) == 1
