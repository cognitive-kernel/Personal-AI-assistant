import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, learning, knowledge_lifecycle
from app.main import app


@pytest.fixture
def lifecycle_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return TestClient(app, client=("127.0.0.1", 12345))


def _candidate(job_id: int, *, status: str = "awaiting_test") -> tuple[int, str]:
    content = "Temporary lesson requiring independent source and model review."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with db.learning_connection() as connection:
        connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, ?, 'unverified', ?)""",
            ("Lifecycle topic", content, "web_retrieved", 1, job_id),
        )
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status, test_results_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "Lifecycle topic", content, "web_retrieved",
             json.dumps(["https://docs.example.test/topic"]),
             digest, json.dumps({"accepted": True}), status, "[]"),
        )
        return int(cursor.lastrowid), digest


def test_candidate_readiness_fails_closed_without_evidence(lifecycle_store):
    job = learning.create_job("Lifecycle candidate")
    candidate_id, digest = _candidate(job["id"])

    result = knowledge_lifecycle.candidate_readiness(candidate_id)

    assert result["candidate_sha256"] == digest
    assert result["ready_for_promotion"] is False
    assert result["status"] == "awaiting_evidence"
    assert result["checks"]["two_supporting_source_hosts"] is False
    assert result["checks"]["controlled_test_passed"] is False


def test_source_review_requires_live_content_hash(lifecycle_store, monkeypatch):
    job = learning.create_job("Source review")
    candidate_id, digest = _candidate(job["id"])
    live_text = "Current source content for a candidate review."
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *args, **kwargs: SimpleNamespace(text=live_text),
    )

    with pytest.raises(ValueError, match="does not match"):
        knowledge_lifecycle.record_candidate_review(
            candidate_id,
            candidate_sha256=digest,
            review_kind="source",
            verdict="supports",
            reviewer_id="source-reviewer",
            note="The source was inspected for direct relevance to this candidate.",
            source_url="https://docs.example.test/topic",
            source_sha256="0" * 64,
        )


def test_source_review_binds_live_hash_and_candidate_hash(lifecycle_store, monkeypatch):
    job = learning.create_job("Bound source review")
    candidate_id, digest = _candidate(job["id"])
    live_text = "Current source content for a candidate review."
    source_digest = hashlib.sha256(live_text.encode("utf-8")).hexdigest()
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *args, **kwargs: SimpleNamespace(text=live_text),
    )

    saved = knowledge_lifecycle.record_candidate_review(
        candidate_id,
        candidate_sha256=digest,
        review_kind="source",
        verdict="supports",
        reviewer_id="source-reviewer",
        note="The source was inspected for direct relevance to this candidate.",
        source_url="https://docs.example.test/topic",
        source_sha256=source_digest,
    )

    assert saved["candidate_sha256"] == digest
    assert saved["source_sha256"] == source_digest
    assert saved["source_host"] == "docs.example.test"


def test_public_api_cannot_forge_ai_review_evidence(lifecycle_store):
    job = learning.create_job("Reject forged reviewer")
    candidate_id, digest = _candidate(job["id"])

    response = lifecycle_store.post(
        f"/api/learning/candidates/{candidate_id}/evidence",
        json={
            "candidate_sha256": digest,
            "review_kind": "ai_review",
            "verdict": "supports",
            "reviewer_id": "claimed-independent-model",
            "model_id": "claimed-model",
            "input_sha256": "1" * 64,
            "output_sha256": "2" * 64,
            "note": "This public request must not be allowed to forge a model review.",
        },
    )

    assert response.status_code == 422


def test_promotion_refuses_stale_hash_and_keeps_candidate(lifecycle_store):
    job = learning.create_job("Stale candidate")
    candidate_id, digest = _candidate(job["id"])

    with pytest.raises(ValueError, match="not met every"):
        knowledge_lifecycle.promote_candidate(
            candidate_id, expected_candidate_sha256=digest
        )

    with db.learning_connection() as connection:
        candidate = connection.execute(
            "SELECT promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        knowledge = connection.execute(
            "SELECT status FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchone()
    assert candidate["promoted_knowledge_id"] is None
    assert knowledge["status"] == "unverified"


def test_review_schema_is_registered_and_indexed(lifecycle_store):
    with db.learning_connection() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_candidate_reviews'"
        ).fetchone()
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_candidate_reviews_candidate'"
        ).fetchone()
    assert table is not None
    assert index is not None
