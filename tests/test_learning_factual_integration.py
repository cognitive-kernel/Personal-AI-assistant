import hashlib
import json

import pytest

from app import claim_review, db, learning


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_python_lesson_review_persists_claims_as_unverified_factual_knowledge(
    learning_store, monkeypatch
):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python lists are mutable sequences. Items can be changed after creation, "
        "and list order is preserved for indexed access."
    )
    source_chunk = f"Source title: Python sequence types\nSource URL: {url}\nContent:\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    source_text = source_chunk

    def discover(_job_id, _topic):
        with db.learning_connection() as conn:
            conn.execute(
                """INSERT INTO learning_evidence(
                       job_id, evidence_type, source_ref, content_hash, note
                   ) VALUES (?, 'web_page', ?, ?, ?)""",
                (_job_id, url, source_hash, "Retrieved source; not independently verified."),
            )
        return source_text, "web_retrieved"

    lesson_result = {
        "lesson": "Python lists are mutable ordered sequences.",
        "learning_outcomes": ["Explain list mutability", "Read an indexed item", "Describe list ordering"],
        "questions": ["What is a list?"],
        "answer_key": ["A mutable ordered sequence."],
    }
    monkeypatch.setattr(learning, "_discover_and_store_sources", discover)
    monkeypatch.setattr(
        learning,
        "_request",
        lambda *_args, **_kwargs: {"message": {"content": json.dumps(lesson_result)}},
    )
    monkeypatch.setattr(
        learning,
        "evaluate_learning_value",
        lambda *_args, **_kwargs: {"accepted": True, "score": 95, "reasons": []},
    )
    monkeypatch.setattr(
        claim_review,
        "review_answer_claims",
        lambda *_args, **_kwargs: {
            "passed": True,
            "overall": "supported",
            "claims": [{
                "claim": "Python lists are mutable sequences.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "The source excerpt directly describes list mutability.",
            }],
            "source_conflicts": [],
            "error": None,
            "reviewer": "local_ollama_model_assisted_not_independent",
        },
    )

    job = learning.create_job("Python list behavior")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    assert finished["status"] == "completed"

    with db.learning_connection() as conn:
        claims = conn.execute(
            "SELECT id, claim, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchall()
        evidence = conn.execute(
            """SELECT source_ref, source_hash, excerpt, applicability_json
               FROM factual_evidence WHERE knowledge_id=?""",
            (claims[0]["id"],),
        ).fetchall()

    assert len(claims) == 1
    assert claims[0]["claim"] == "Python lists are mutable sequences."
    # A model-assisted pass is recorded as provenance, never as automatic promotion.
    assert claims[0]["evidence_level"] == "L0_raw"
    assert claims[0]["lifecycle_status"] == "unverified"
    assert len(evidence) == 1
    assert evidence[0]["source_ref"] == url
    assert evidence[0]["source_hash"] == source_hash
    assert evidence[0]["excerpt"] == excerpt
    applicability = json.loads(evidence[0]["applicability_json"])
    assert applicability["model_assisted_review_only"] is True
    assert applicability["independent_verification"] is False
