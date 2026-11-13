import hashlib
import json

from app import db, learning
from scripts.repair_legacy_rejected_candidates import repair_legacy_rejected_candidates


def _seed_legacy_candidate(job_id: int, *, content_hash: str, lesson: str, source_url: str):
    quality = {"lesson_retained": True, "retention_policy": "legacy-recovery-test"}
    test_results = [{"name": "old-result", "passed": False}]
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET lesson=?, quality_json=?, assessment_json=NULL WHERE id=?",
            (lesson, json.dumps(quality), job_id),
        )
        connection.execute(
            """INSERT INTO learning_evidence(job_id, evidence_type, source_ref, content_hash, note)
               VALUES (?, 'web_page', ?, ?, ?)""",
            (
                job_id,
                source_url,
                hashlib.sha256(b"preserved source evidence").hexdigest(),
                "Preserved source evidence metadata for exact recovery testing.",
            ),
        )
        candidate_id = int(connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json, content_sha256,
                   quality_report_json, status, test_plan_json, test_results_json, failure_reason
               ) VALUES (?, ?, '', 'web_retrieved', '[]', ?, '{}', 'failed', '[]', ?, ?)""",
            (
                job_id,
                "Legacy rejected candidate",
                content_hash,
                json.dumps(test_results),
                "rejected_after_review: legacy candidate scrubbed by previous implementation.",
            ),
        ).lastrowid)
    return candidate_id, quality, test_results


def test_legacy_repair_is_dry_run_by_default_and_restores_only_verified_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    lesson = "Exact original candidate content retained in the durable learning job."
    job = learning.create_job("Legacy rejected candidate")
    candidate_id, quality, test_results = _seed_legacy_candidate(
        job["id"],
        content_hash=hashlib.sha256(lesson.encode("utf-8")).hexdigest(),
        lesson=lesson,
        source_url="https://docs.example.org/legacy-source",
    )

    dry_run = repair_legacy_rejected_candidates()
    assert dry_run["mode"] == "dry_run"
    assert dry_run["updated"] == 0
    assert dry_run["candidates"][0]["recovered_fields"] == [
        "content",
        "source_urls",
        "quality_report",
    ]
    with db.learning_connection() as connection:
        before = connection.execute(
            """SELECT content, source_urls_json, quality_report_json, test_results_json
               FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
    assert before["content"] == ""
    assert before["source_urls_json"] == "[]"
    assert before["quality_report_json"] == "{}"

    applied = repair_legacy_rejected_candidates(apply=True)
    assert applied["mode"] == "apply"
    assert applied["updated"] == 1
    with db.learning_connection() as connection:
        row = connection.execute(
            """SELECT status, content, source_urls_json, quality_report_json,
                      test_plan_json, test_results_json, failure_reason
               FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
        job_row = connection.execute(
            "SELECT assessment_json FROM learning_jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        source_text_exists = connection.execute(
            "SELECT 1 FROM learning_source_text WHERE job_id=?",
            (job["id"],),
        ).fetchone()
        knowledge_item_exists = connection.execute(
            "SELECT 1 FROM knowledge_items WHERE job_id=?",
            (job["id"],),
        ).fetchone()

    assert row["status"] == "failed"
    assert row["content"] == lesson
    assert json.loads(row["source_urls_json"]) == ["https://docs.example.org/legacy-source"]
    assert json.loads(row["quality_report_json"]) == quality
    assert json.loads(row["test_plan_json"]) == []
    assert json.loads(row["test_results_json"]) == test_results
    assert "legacy_recovery:" in row["failure_reason"]
    assert job_row["assessment_json"] is None
    assert source_text_exists is None
    assert knowledge_item_exists is None

    readiness = __import__("app.knowledge_lifecycle", fromlist=["candidate_readiness"]).candidate_readiness(candidate_id)
    assert readiness["status"] == "rejected_after_review"
    assert readiness["checks"]["content_hash_valid"] is True
    assert readiness["checks"]["legacy_partial_recovery"] is True
    assert readiness["checks"]["promotion_blocked"] is True
    assert readiness["checks"]["source_text_retained"] is False
    assert readiness["checks"]["assessment_retained"] is False
    assert readiness["checks"]["test_plan_retained"] is False

    repeated = repair_legacy_rejected_candidates(apply=True)
    assert repeated["updated"] == 0


def test_legacy_repair_never_restores_content_when_job_hash_does_not_match(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    expected = "Expected candidate text whose hash was retained."
    job = learning.create_job("Legacy hash mismatch")
    candidate_id, _quality, _test_results = _seed_legacy_candidate(
        job["id"],
        content_hash=hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        lesson="Different lesson content must never be substituted.",
        source_url="https://docs.example.org/verified-source",
    )

    result = repair_legacy_rejected_candidates(apply=True)
    assert result["updated"] == 1
    assert result["candidates"][0]["content_hash_verified"] is False
    assert "content" not in result["candidates"][0]["recovered_fields"]

    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT content, source_urls_json, quality_report_json FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert row["content"] == ""
    assert json.loads(row["source_urls_json"]) == ["https://docs.example.org/verified-source"]
    assert json.loads(row["quality_report_json"])["lesson_retained"] is True
