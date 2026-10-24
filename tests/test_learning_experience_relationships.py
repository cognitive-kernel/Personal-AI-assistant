import hashlib
import json

import pytest

from app import db, learning, learning_candidate_experiences


@pytest.fixture
def candidate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Relationship test", source_text="Reference text.")
    content = "A staged lesson whose failures remain auditable."
    with db.learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], "Relationship test", content, "user_provided_text",
             json.dumps(["https://docs.python.org/3/"]),
             hashlib.sha256(content.encode("utf-8")).hexdigest(),
             json.dumps({"accepted": True, "score": 90})),
        )
        return int(cursor.lastrowid), content


def test_channel_failure_experience_persists_direct_relationships(candidate_store):
    candidate_id, content = candidate_store
    result = learning_candidate_experiences.record_candidate_channel_failure(
        candidate_id,
        topic="Relationship test",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        channel="peer_ai",
        status="unavailable",
        error_type="ConnectionError",
        related_knowledge_id=41,
        related_consensus_id=72,
    )
    assert result["created"] is True
    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT related_knowledge_id, related_consensus_id FROM learning_experiences WHERE id=?",
            (result["experience_id"],),
        ).fetchone()
    assert row["related_knowledge_id"] == 41
    assert row["related_consensus_id"] == 72


@pytest.mark.parametrize(
    "field,value",
    [("related_knowledge_id", 0), ("related_knowledge_id", True),
     ("related_consensus_id", -1), ("related_consensus_id", "72")],
)
def test_channel_failure_rejects_invalid_relationship_ids(candidate_store, field, value):
    candidate_id, content = candidate_store
    kwargs = {"related_knowledge_id": None, "related_consensus_id": None}
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        learning_candidate_experiences.record_candidate_channel_failure(
            candidate_id,
            topic="Relationship test",
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            channel="peer_ai",
            status="failed",
            **kwargs,
        )


def test_test_failure_experience_persists_direct_relationships(candidate_store):
    candidate_id, content = candidate_store
    result = learning_candidate_experiences.record_candidate_test_failure(
        candidate_id,
        topic="Relationship test",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        test_plan_sha256=hashlib.sha256(b"reviewed-plan").hexdigest(),
        outcomes=[{"test": "sample", "passed": False}],
        related_knowledge_id=123,
        related_consensus_id=456,
    )
    assert result["created"] is True
    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT related_knowledge_id, related_consensus_id FROM learning_experiences WHERE id=?",
            (result["experience_id"],),
        ).fetchone()
    assert row["related_knowledge_id"] == 123
    assert row["related_consensus_id"] == 456


@pytest.mark.parametrize(
    "field,value",
    [("related_knowledge_id", 0), ("related_knowledge_id", True),
     ("related_consensus_id", -1), ("related_consensus_id", "456")],
)
def test_test_failure_rejects_invalid_relationship_ids(candidate_store, field, value):
    candidate_id, content = candidate_store
    kwargs = {"related_knowledge_id": None, "related_consensus_id": None}
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        learning_candidate_experiences.record_candidate_test_failure(
            candidate_id,
            topic="Relationship test",
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            test_plan_sha256=hashlib.sha256(b"reviewed-plan").hexdigest(),
            outcomes=[{"test": "sample", "passed": False}],
            **kwargs,
        )


def test_failure_experience_auto_links_to_same_job_lesson(candidate_store):
    candidate_id, content = candidate_store
    with db.learning_connection() as connection:
        job_id = connection.execute(
            "SELECT job_id FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["job_id"]
        knowledge_id = connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES ('Relationship test', 'Staged lesson body', 'user_provided_text', 1, 'unverified', ?)""",
            (job_id,),
        ).lastrowid

    result = learning_candidate_experiences.record_candidate_channel_failure(
        candidate_id,
        topic="Relationship test",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        channel="peer_ai",
        status="unavailable",
    )
    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT related_knowledge_id FROM learning_experiences WHERE id=?",
            (result["experience_id"],),
        ).fetchone()
    assert row["related_knowledge_id"] == knowledge_id
