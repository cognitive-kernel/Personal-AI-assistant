import hashlib

import pytest
from fastapi import HTTPException

from app import db, knowledge_lifecycle, learning
from app.learning_candidate_routes import get_learning_candidate, get_learning_candidate_claims


def _seed_hash_invalid_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Hash-invalid retained candidate")
    expected = "Original candidate text represented by the immutable digest."
    corrupted = "Non-empty retained content whose digest no longer matches."
    expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    with db.learning_connection() as connection:
        candidate_id = int(connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json, content_sha256,
                   quality_report_json, status, test_plan_json, test_results_json, failure_reason
               ) VALUES (?, ?, ?, 'model_generated', '[]', ?, '{}', 'awaiting_test', '[]', '[]', ?)""",
            (
                job["id"],
                "Hash-invalid candidate",
                corrupted,
                expected_hash,
                "content integrity failure retained for audit",
            ),
        ).lastrowid)
    return candidate_id, expected_hash, corrupted


def test_hash_invalid_candidate_content_is_readable_but_not_reviewable_or_promotable(tmp_path, monkeypatch):
    candidate_id, expected_hash, corrupted = _seed_hash_invalid_candidate(tmp_path, monkeypatch)

    detail = get_learning_candidate(candidate_id)["candidate"]
    assert detail["content"] == corrupted
    assert detail["integrity_valid"] is False

    readiness = knowledge_lifecycle.candidate_readiness(candidate_id)
    assert readiness["status"] == "integrity_mismatch"
    assert readiness["ready_for_promotion"] is False
    assert readiness["checks"]["content_hash_valid"] is False
    assert readiness["checks"]["content_retained"] is True
    assert readiness["checks"]["promotion_blocked"] is True

    with pytest.raises(HTTPException) as error:
        get_learning_candidate_claims(candidate_id)
    assert error.value.status_code == 409

    with pytest.raises(ValueError, match="Candidate content hash mismatch"):
        knowledge_lifecycle.promote_candidate(
            candidate_id,
            expected_candidate_sha256=expected_hash,
        )
