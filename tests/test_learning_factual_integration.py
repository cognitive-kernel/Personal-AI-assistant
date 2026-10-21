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


def test_repeated_factual_review_delivery_is_idempotent(learning_store):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python dictionaries map unique keys to values. Keys are used to retrieve "
        "the corresponding values from a mapping."
    )
    source_chunk = f"Source title: Mapping types\\nSource URL: {url}\\nContent:\\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Python dictionaries map keys to values.",
            "verdict": "supported",
            "evidence_urls": [url],
        }],
    }
    job = learning.create_job("Python dictionaries")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, source_hash, "Captured source; not independently verified."),
        )
        first_count = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python dictionaries",
            source_text=source_chunk, review=review,
        )
        second_count = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python dictionaries",
            source_text=source_chunk, review=review,
        )
        claim_rows = conn.execute(
            "SELECT id, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchall()
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM factual_evidence"
        ).fetchone()[0]

    assert first_count == second_count == 1
    assert len(claim_rows) == 1
    assert claim_rows[0]["evidence_level"] == "L0_raw"
    assert claim_rows[0]["lifecycle_status"] == "unverified"
    assert evidence_count == 1


def test_review_cannot_attach_evidence_from_an_unmatched_url(learning_store):
    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python tuples are immutable sequences. Their items cannot be replaced "
        "after the tuple has been created."
    )
    source_chunk = f"Source title: Sequence types\\nSource URL: {url}\\nContent:\\n{excerpt}"
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Python tuples are immutable sequences.",
            "verdict": "supported",
            "evidence_urls": ["https://example.invalid/fabricated-source"],
        }],
    }
    job = learning.create_job("Python tuples")
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, source_hash, "Captured source; not independently verified."),
        )
        stored = learning._persist_factual_review_claims(
            conn, job_id=job["id"], topic="Python tuples",
            source_text=source_chunk, review=review,
        )
        claim = conn.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchone()
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM factual_evidence"
        ).fetchone()[0]

    assert stored == 1
    assert claim["evidence_level"] == "L0_raw"
    assert claim["lifecycle_status"] == "unverified"
    assert evidence_count == 0


def test_factual_review_integration_remains_scoped_to_python(learning_store):
    job = learning.create_job("Docker container networking")
    review = {
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "Docker bridge networks provide container connectivity.",
            "verdict": "supported",
            "evidence_urls": ["https://docs.docker.com/network/"],
        }],
    }
    with db.learning_connection() as conn:
        stored = learning._persist_factual_review_claims(
            conn,
            job_id=job["id"],
            topic="Docker container networking",
            source_text=(
                "Source title: Docker networking\\n"
                "Source URL: https://docs.docker.com/network/\\n"
                "Content:\\nDocker bridge networks connect containers."
            ),
            review=review,
        )
        claim_count = conn.execute(
            "SELECT COUNT(*) FROM factual_knowledge"
        ).fetchone()[0]

    assert stored == 0
    assert claim_count == 0
