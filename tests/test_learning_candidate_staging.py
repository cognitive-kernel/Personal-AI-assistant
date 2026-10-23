import hashlib
import json

import pytest

from app import db, learning


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _configure_model(monkeypatch, *, accepted):
    lesson = "A generated lesson kept as a candidate until evidence and trusted tests are reviewed."
    response = {
        "message": {
            "content": json.dumps({
                "lesson": lesson,
                "learning_outcomes": [
                    "Explain the main mechanism",
                    "Identify the relevant constraint",
                    "Apply the method to a concrete example",
                ],
                "questions": [
                    "What is the main mechanism?",
                    "Which constraint matters?",
                    "How can the method be applied?",
                ],
                "answer_key": [
                    "It coordinates the relevant processing steps.",
                    "The declared environment and evidence determine the constraint.",
                    "Apply the method to a bounded example and inspect its result.",
                ],
            })
        }
    }
    monkeypatch.setattr(learning, "_request", lambda *args, **kwargs: response)
    monkeypatch.setattr(learning, "_verified_factual_context_for_topic", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(learning, "detect_hardware", lambda: {
        "logical_cpu_count": 4,
        "total_ram_gb": 16,
        "available_ram_gb": 12,
    })
    monkeypatch.setattr(learning, "evaluate_learning_value", lambda *args, **kwargs: {
        "score": 90 if accepted else 40,
        "accepted": accepted,
        "reasons": [] if accepted else ["injected rejected quality result"],
        "word_count": 210,
    })
    return lesson


def test_accepted_lesson_is_staged_as_unverified_candidate(learning_store, monkeypatch):
    lesson = _configure_model(monkeypatch, accepted=True)
    job = learning.create_job("Candidate staging", source_text="User-provided reference text.")

    learning.run_job(job["id"])

    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT * FROM temporary_knowledge_candidates WHERE job_id=?",
            (job["id"],),
        ).fetchone()
        knowledge = conn.execute(
            "SELECT * FROM knowledge_items WHERE job_id=?",
            (job["id"],),
        ).fetchone()

    assert candidate is not None
    assert candidate["status"] == "awaiting_test"
    assert candidate["content"] == lesson
    assert candidate["content_sha256"] == hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    assert json.loads(candidate["source_urls_json"]) == []
    assert json.loads(candidate["quality_report_json"])["accepted"] is True
    assert candidate["promoted_knowledge_id"] is None
    assert knowledge is not None
    assert knowledge["status"] == "unverified"


def test_rejected_lesson_is_not_staged_as_candidate(learning_store, monkeypatch):
    _configure_model(monkeypatch, accepted=False)
    job = learning.create_job("Rejected candidate staging", source_text="User-provided reference text.")

    learning.run_job(job["id"])

    saved = learning.get_job(job["id"])
    with db.learning_connection() as conn:
        candidates = conn.execute(
            "SELECT COUNT(*) FROM temporary_knowledge_candidates WHERE job_id=?",
            (job["id"],),
        ).fetchone()[0]

    assert saved["status"] == "rejected"
    assert candidates == 0
