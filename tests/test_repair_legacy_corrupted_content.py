import hashlib

from app import db, learning
from scripts.repair_legacy_rejected_candidates import repair_legacy_rejected_candidates


def test_repair_finds_corrupted_content_even_when_metadata_is_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    original = "The exact lesson text retained by the durable learning job."
    job = learning.create_job("Legacy corrupted candidate")
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET lesson=? WHERE id=?",
            (original, job["id"]),
        )
        candidate_id = int(connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json, content_sha256,
                   quality_report_json, status, test_plan_json, test_results_json, failure_reason
               ) VALUES (?, ?, ?, 'web_retrieved', ?, ?, ?, 'failed', '[]', '[]', ?)""",
            (
                job["id"],
                "Legacy corrupted candidate",
                "non-empty corrupted content",
                '["https://preserved.example/source"]',
                hashlib.sha256(original.encode("utf-8")).hexdigest(),
                '{"already_preserved": true}',
                "rejected_after_review: legacy candidate scrubbed by previous implementation.",
            ),
        ).lastrowid)

    dry_run = repair_legacy_rejected_candidates()
    assert dry_run["updated"] == 0
    assert dry_run["scanned"] == 1
    assert dry_run["candidates"][0]["content_hash_verified"] is True
    assert dry_run["candidates"][0]["recovered_fields"] == ["content"]

    with db.learning_connection() as connection:
        unchanged = connection.execute(
            "SELECT content, source_urls_json, quality_report_json FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert unchanged["content"] == "non-empty corrupted content"
    assert unchanged["source_urls_json"] == '["https://preserved.example/source"]'
    assert unchanged["quality_report_json"] == '{"already_preserved": true}'

    applied = repair_legacy_rejected_candidates(apply=True)
    assert applied["updated"] == 1
    assert applied["candidates"][0]["content_hash_verified"] is True

    with db.learning_connection() as connection:
        repaired = connection.execute(
            "SELECT content, content_sha256, source_urls_json, quality_report_json, failure_reason "
            "FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert repaired["content"] == original
    assert hashlib.sha256(repaired["content"].encode("utf-8")).hexdigest() == repaired["content_sha256"]
    assert repaired["source_urls_json"] == '["https://preserved.example/source"]'
    assert repaired["quality_report_json"] == '{"already_preserved": true}'
    assert "legacy_recovery:" in repaired["failure_reason"]
