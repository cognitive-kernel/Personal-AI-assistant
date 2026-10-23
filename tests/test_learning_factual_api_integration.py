import hashlib
import json

from fastapi.testclient import TestClient

from app import claim_review, db, learning, task_manager
from app.main import app


def test_learning_api_job_to_factual_claim_preserves_provenance_without_promotion(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()

    url = "https://docs.python.org/3/library/stdtypes.html"
    excerpt = (
        "Python lists are mutable sequences. Items can be changed after creation, "
        "and list order is preserved for indexed access."
    )
    source_chunk = (
        f"Source title: Python sequence types\nSource URL: {url}\nContent:\n{excerpt}"
    )
    source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()

    def discover(job_id, _topic):
        with db.learning_connection() as connection:
            connection.execute(
                """INSERT INTO learning_evidence(
                       job_id, evidence_type, source_ref, content_hash, note
                   ) VALUES (?, 'web_page', ?, ?, ?)""",
                (job_id, url, source_hash, "Captured source; not independently verified."),
            )
        return source_chunk, "web_retrieved"

    result = {
        "lesson": "Python lists are mutable ordered sequences.",
        "learning_outcomes": ["Explain mutability", "Read an item", "Describe ordering"],
        "questions": ["What is a list?"],
        "answer_key": ["A mutable ordered sequence."],
    }
    monkeypatch.setattr(learning, "_discover_and_store_sources", discover)
    monkeypatch.setattr(
        learning, "_enqueue_learning_job", lambda _job_id: {"id": 987654}
    )
    monkeypatch.setattr(
        learning,
        "_request",
        lambda *_args, **_kwargs: {"message": {"content": json.dumps(result)}},
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
                "evidence_quotes": [{"url": url, "quote": excerpt}],
                "reason": "The cited excerpt describes list mutability.",
            }],
            "source_conflicts": [],
            "error": None,
            "reviewer": "local_ollama_model_assisted_not_independent",
        },
    )

    # Do not enter TestClient's lifespan: this test drives the job explicitly and
    # must not start a background worker that races the assertions.
    client = TestClient(app, client=("127.0.0.1", 12345))
    response = client.post(
        "/api/learning/jobs",
        json={"topic": "Python list behavior"},
    )

    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "queued"
    assert created["task_id"] == 987654

    learning.run_job(created["id"])
    finished = learning.get_job(created["id"])
    assert finished["status"] == "completed"

    with db.learning_connection() as connection:
        claims = connection.execute(
            "SELECT id, claim, evidence_level, lifecycle_status FROM factual_knowledge"
        ).fetchall()
        evidence = connection.execute(
            """SELECT source_type, source_ref, source_hash, applicability_json
               FROM factual_evidence WHERE knowledge_id=?""",
            (claims[0]["id"],),
        ).fetchall()

    assert len(claims) == 1
    assert claims[0]["claim"] == "Python lists are mutable sequences."
    assert claims[0]["evidence_level"] == "L0_raw"
    assert claims[0]["lifecycle_status"] == "unverified"
    assert len(evidence) == 1
    assert evidence[0]["source_type"] == "official_docs"
    assert evidence[0]["source_ref"] == url
    assert evidence[0]["source_hash"] == source_hash
    applicability = json.loads(evidence[0]["applicability_json"])
    assert applicability["model_assisted_review_only"] is True
    assert applicability["independent_verification"] is False
