import hashlib
import json

import pytest

from app import db, learning
from app.learning_consensus import start_self_correction


def _failed_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud.db"))
    db.init_databases()
    job = learning.create_job(
        "candidate with a failed claim",
        source_text="Source retained for re-research and comparison.",
    )
    content = "This failed technical claim must be rewritten with a corrected condition and test."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        candidate_id = int(conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status, trust_level,
                   learning_status, failure_reason, test_results_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', 'L-1', 'failed', ?, ?)""",
            (job["id"], job["topic"], content, "user_provided_text", "[]",
             digest, json.dumps({"accepted": True}),
             "A previously recorded test failed.", json.dumps([{"test": "edge-case", "passed": False}])),
        ).lastrowid)
    return candidate_id, content


def test_self_correction_creates_new_l0_candidate_and_keeps_failed_parent(tmp_path, monkeypatch):
    candidate_id, original = _failed_candidate(tmp_path, monkeypatch)
    alternative = (
        "The corrected technical claim applies only when the input is normalized and its bounds "
        "are checked before execution. The alternative explicitly changes the precondition and requires regression tests."
    )
    monkeypatch.setattr(
        "app.model_runtime.chat",
        lambda *_args, **_kwargs: json.dumps({
            "lesson": alternative,
            "rationale": "The earlier claim omitted the input precondition.",
            "changed_claims": ["Added an explicit input precondition."],
            "limitations": ["The change must still pass the declared test suite."],
            "suggested_tests": ["Run the failing edge case after normalization."],
        }),
    )
    seen = {}
    def fake_hybrid(child_id, **kwargs):
        seen["candidate_id"] = child_id
        return {"status": "completed", "consensus": {"outcome": "insufficient_evidence"}}
    monkeypatch.setattr("app.hybrid_learning.run_hybrid_verification", fake_hybrid)

    result = start_self_correction(candidate_id)

    assert result["alternative_candidate_id"] == seen["candidate_id"]
    assert result["trust_level"] == "L0"
    assert result["previous_candidate_preserved"] is True
    with db.learning_connection() as conn:
        parent = conn.execute(
            "SELECT content, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        child = conn.execute(
            """SELECT content, trust_level, learning_status, supersedes_candidate_id
               FROM temporary_knowledge_candidates WHERE id=?""",
            (result["alternative_candidate_id"],),
        ).fetchone()
        attempt = conn.execute(
            "SELECT status, attempt_no, alternative_candidate_id FROM knowledge_self_correction_attempts WHERE id=?",
            (result["attempt_id"],),
        ).fetchone()
    assert tuple(parent) == (original, "L-1", "failed")
    assert tuple(child) == (alternative, "L0", "unverified", candidate_id)
    assert tuple(attempt) == ("alternative_created", 1, result["alternative_candidate_id"])


def test_self_correction_is_limited_to_three_attempts(tmp_path, monkeypatch):
    candidate_id, _ = _failed_candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        for attempt_no in (1, 2, 3):
            conn.execute(
                """INSERT INTO knowledge_self_correction_attempts(
                       candidate_id, candidate_sha256, attempt_no, prompt_version, prompt, status
                   ) VALUES (?, ?, ?, 'fixture-v1', '{}', 'failed')""",
                (candidate_id, "a" * 64, attempt_no),
            )
    with pytest.raises(ValueError, match="maximum of three"):
        start_self_correction(candidate_id)
