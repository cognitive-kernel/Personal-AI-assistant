import hashlib

import pytest

from app import db, learning
from app.learning_consensus import start_self_correction


def _failed_candidate(topic, parent_id=None):
    job = learning.create_job(topic, source_text="Original source retained for the correction lineage.")
    content = f"Failed candidate content for {topic}; requires independent verification."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status, trust_level,
                   learning_status, supersedes_candidate_id, failure_reason
               ) VALUES (?, ?, ?, 'user_provided_text', '[]', ?, '{}', 'failed', 'L-1',
                         'conflicting', ?, 'test failure')""",
            (job["id"], topic, content, parent_id),
        )
        return int(cursor.lastrowid), digest


def test_self_correction_limit_is_shared_across_candidate_lineage(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()

    root_id, root_hash = _failed_candidate("lineage root")
    child_id, child_hash = _failed_candidate("lineage child", root_id)
    leaf_id, leaf_hash = _failed_candidate("lineage leaf", child_id)

    with db.learning_connection() as conn:
        for candidate_id, digest in (
            (root_id, root_hash), (child_id, child_hash), (leaf_id, leaf_hash),
        ):
            conn.execute(
                """INSERT INTO knowledge_self_correction_attempts(
                       candidate_id, candidate_sha256, attempt_no, prompt_version,
                       prompt, status, failure_reason, details_json
                   ) VALUES (?, ?, 1, 'test-prompt-v1', '{}', 'alternative_created', 'test', '{}')""",
                (candidate_id, digest),
            )

    with pytest.raises(ValueError, match="three self-correction attempts per knowledge lineage"):
        start_self_correction(leaf_id)
