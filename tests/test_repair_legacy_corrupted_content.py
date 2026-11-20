import hashlib
import json

from app import db, learning
from scripts.repair_legacy_rejected_candidates import repair_legacy_rejected_candidates


def test_repair_replaces_corrupted_nonempty_content_only_from_exact_hash_match(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    original = "The exact lesson text retained by the durable learning job."
    job = learning.create_job("Legacy corrupted candidate")
    with db.learning_connection() as connection:
        candidate_id = int(connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json, content_sha256,
                   quality_report_json, status, test_plan_json, test_results_json, failure_reason
               ) VALUES (?, ?, ?, 'web_retrieved', '[]', ?, '{}', 'failed', '[]', '[]', ?)""",
            (
                job["id"],
                "Legacy corrupted candidate",
                "non-empty corrupted content",
                hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "rejected_after_review: legacy candidate scrubbed by previous implementation.",
            ),
        ).lastrowid)

    dry_run = repair_legacy_rejected_candidates()
    assert dry_run["updated"] == 0
    assert dry_run["candidates"][0]["content_hash_verified"] is True
    assert "content" in dry_run["candidates"][0]["recovered_fields"]

    with db.learning_connection() as connection:
        unchanged = connection.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert unchanged["content"] == "non-empty corrupted content"

    applied = repair_legacy_rejected_candidates(apply=True)
    assert applied["updated"] == 1
    assert applied["candidates"][0]["content_hash_verified"] is True

    with db.learning_connection() as connection:
        repaired = connection.execute(
            "SELECT content, content_sha256, failure_reason FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert repaired["content"] == original
    assert hashlib.sha256(repaired["content"].encode("utf-8")).hexdigest() == repaired["content_sha256"]
    assert "legacy_recovery:" in repaired["failure_reason"]
