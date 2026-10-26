from __future__ import annotations
import hashlib
import sqlite3
import pytest
from app.ai_learning import service
from app.ai_learning.db import initialize_store, connection, StoreSchemaError

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "ai_learning.db"
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(path))
    initialize_store()
    return path

def test_topic_approval_lesson_version_review_and_publish(isolated_store):
    course = service.create_course(title="SQL Server 2005")
    topic = service.add_topic(course["id"], "Indexes")
    with pytest.raises(ValueError, match="approved topic"):
        service.create_lesson(topic["id"], "Index basics")
    assert service.decide_topics([topic["id"]], "approved") == 1
    lesson = service.create_lesson(topic["id"], "Index basics")
    version = service.add_content_version(lesson["id"], "# Indexes\n\nAn index helps locate rows efficiently.")
    assert version["review_status"] == "needs_review"
    with pytest.raises(ValueError, match="reviewed"):
        service.publish_content(version["id"])
    service.review_content(version["id"], "reviewed")
    published = service.publish_content(version["id"])
    assert published["review_status"] == "published"
    results = service.search_knowledge("index efficiently")
    assert results["count"] == 1
    item = results["items"][0]
    assert item["lesson_id"] == lesson["id"]
    assert item["content_version_id"] == version["id"]
    assert item["content_sha256"] == hashlib.sha256(b"An index helps locate rows efficiently.").hexdigest()

def test_search_excludes_unpublished_content(isolated_store):
    course = service.create_course(title="Python")
    topic = service.add_topic(course["id"], "Typing")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Type hints")
    service.add_content_version(lesson["id"], "Type hints annotate Python interfaces.")
    assert service.search_knowledge("Python interfaces")["count"] == 0

def test_reorder_requires_exact_topic_set(isolated_store):
    course = service.create_course(title="Databases")
    first = service.add_topic(course["id"], "First")
    second = service.add_topic(course["id"], "Second")
    ordered = service.reorder_topics(course["id"], [second["id"], first["id"]])
    assert [item["id"] for item in ordered] == [second["id"], first["id"]]
    with pytest.raises(ValueError, match="every active topic"):
        service.reorder_topics(course["id"], [first["id"]])

def test_migration_checksum_mismatch_fails_closed(isolated_store):
    with connection() as conn:
        conn.execute("UPDATE ai_learning_migrations SET checksum='invalid' WHERE version=1")
    with pytest.raises(StoreSchemaError, match="checksum mismatch"):
        initialize_store()

def test_unknown_existing_schema_is_not_adopted(tmp_path, monkeypatch):
    path = tmp_path / "unknown.db"
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE mystery (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(StoreSchemaError, match="no migration history"):
        initialize_store()

def test_rejected_topic_is_removed_from_retrieval(isolated_store):
    course = service.create_course(title="Python security")
    topic = service.add_topic(course["id"], "Input validation")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Parameterized SQL")
    version = service.add_content_version(lesson["id"], "Parameterized SQL prevents injection.")
    service.review_content(version["id"], "reviewed")
    service.publish_content(version["id"])
    assert service.search_knowledge("Parameterized injection")["count"] == 1
    service.decide_topics([topic["id"]], "rejected")
    assert service.search_knowledge("Parameterized injection")["count"] == 0


def test_feature_flags_are_off_by_default(monkeypatch):
    from app.ai_learning import config
    monkeypatch.delenv("AI_LEARNING_ENABLED", raising=False)
    monkeypatch.delenv("AI_LEARNING_RETRIEVAL_ENABLED", raising=False)
    assert config.is_enabled() is False
    assert config.retrieval_is_enabled() is False

def test_outline_import_creates_pending_topics_and_preserves_raw_response(isolated_store):
    course = service.create_course(title="SQL Server 2005")
    raw = "1. Architecture\n2. Indexes\n3. Backup and restore"
    result = service.import_outline(course["id"], raw, provider="chatgpt_ui")
    assert result["imported"] == 3
    assert all(topic["approval_status"] == "pending" for topic in result["topics"])
    assert [topic["title"] for topic in result["topics"]] == ["Architecture", "Indexes", "Backup and restore"]
    with connection() as conn:
        run = conn.execute("SELECT response_text,status FROM runs WHERE id=?", (result["run_id"],)).fetchone()
    assert run["response_text"] == raw
    assert run["status"] == "succeeded"


def test_outline_import_rejects_unstructured_response(isolated_store):
    course = service.create_course(title="SQL Server")
    with pytest.raises(ValueError, match="No numbered"):
        service.import_outline(course["id"], "This is a paragraph without an outline.")
