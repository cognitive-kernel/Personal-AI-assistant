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

def test_run_lifecycle_imports_outline_without_auto_approval(isolated_store):
    course = service.create_course(title="SQL Server")
    run = service.create_run(course["id"], run_type="outline", request_key="outline-test-1")
    assert run["status"] == "queued"
    assert "numbered topics" in run["prompt_text"]
    service.start_run(run["id"])
    finished = service.complete_run(run["id"], "1. Architecture\n2. Query processing")
    assert finished["status"] == "succeeded"
    assert len(finished["topics"]) == 2
    assert all(topic["approval_status"] == "pending" for topic in finished["topics"])


def test_lesson_run_creates_reviewable_version_not_published(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]], request_key="lesson-test-1")
    service.start_run(run["id"])
    finished = service.complete_run(run["id"], "# Index fundamentals\n\nIndexes speed up data access.")
    assert finished["status"] == "succeeded"
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    versions = service.list_versions(lessons[0]["id"])
    assert versions[0]["review_status"] == "needs_review"
    with connection() as conn:
        stored = conn.execute("SELECT run_id,raw_response FROM content_versions WHERE id=?", (versions[0]["id"],)).fetchone()
    assert stored["run_id"] == run["id"]
    assert stored["raw_response"] == "# Index fundamentals\n\nIndexes speed up data access."


def test_batch_run_requires_topic_markers_and_can_retry_mapping(isolated_store):
    course = service.create_course(title="Database topics")
    a = service.add_topic(course["id"], "Indexes")
    b = service.add_topic(course["id"], "Transactions")
    service.decide_topics([a["id"], b["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[a["id"], b["id"]],
                             selection_mode="selected", request_key="batch-test-1")
    service.start_run(run["id"])
    ambiguous = service.complete_run(run["id"], "Here are two sections, but no topic markers.")
    assert ambiguous["status"] == "needs_user_action"
    assert service.list_lessons(a["id"]) == []
    corrected = "[TOPIC_ID:" + str(a["id"]) + "] Index lesson\n\nIndex content.\n\n[TOPIC_ID:" + str(b["id"]) + "] Transaction lesson\n\nTransaction content."
    completed = service.complete_run(run["id"], corrected)
    assert completed["status"] == "succeeded"
    assert len(service.list_lessons(a["id"])) == 1
    assert len(service.list_lessons(b["id"])) == 1

def test_all_approved_run_selects_all_approved_topics(isolated_store):
    course = service.create_course(title="SQL Server")
    approved = service.add_topic(course["id"], "Indexes")
    pending = service.add_topic(course["id"], "Triggers")
    service.decide_topics([approved["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", selection_mode="all_approved",
                             request_key="all-approved-test")
    assert [topic["topic_id"] for topic in run["topics"]] == [approved["id"]]
    assert pending["id"] not in [topic["topic_id"] for topic in run["topics"]]


def test_run_request_key_is_idempotent(isolated_store):
    course = service.create_course(title="SQL Server")
    first = service.create_run(course["id"], run_type="outline", request_key="same-key")
    second = service.create_run(course["id"], run_type="outline", request_key="same-key")
    assert first["id"] == second["id"]

def test_playwright_capture_uses_visible_ui_and_keeps_extension_permissions_narrow():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    extension = root / "app" / "ai_learning" / "browser_extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    assert "<all_urls>" not in manifest.get("host_permissions", [])
    assert not any("cookies" in permission for permission in manifest.get("permissions", []))
    automation = (root / "app" / "ai_learning" / "browser_automation.py").read_text(encoding="utf-8")
    assert 'page.goto("https://chatgpt.com/"' in automation
    assert 'data-message-author-role="assistant"' in automation
    assert "launch_persistent_context" in automation
    assert "api.openai.com" not in automation
    assert "context.cookies(" not in automation
    assert "storage_state(" not in automation
    assert "required_stability = 25.0 if stop_visible else _STABLE_SECONDS" in automation
    assert '"chatgpt_page_closed"' in automation
    assert "Keep the wait inside the guarded block" in automation
    assert "retain that answer instead of discarding it" in automation
    assert "newest non-empty assistant message" in automation
    assert "for index in range(count - 1, -1, -1)" in automation
    assert "def _assistant_message_count(page: Any) -> int:" in automation
    assert "previous_message_count=previous_message_count" in automation
    assert "current != previous_answer or new_message_seen" in automation
    assert "def _log_event(event: str, **fields: Any) -> None:" in automation
    assert '"data/logs/ai_learning_browser.log"' in automation
    assert '"response_poll"' in automation
    assert '"response_accepted"' in automation
    assert '"database_completion_started"' in automation
    assert "RotatingFileHandler" in automation
    assert "def _dom_diagnostics(page: Any) -> dict[str, Any]:" in automation
    assert "bodyTextLength" in automation
    assert "conversationTurns" in automation
    assert '"page_crashed_event"' in automation
    assert '"browser_request_failed"' in automation

def test_opt_in_retrieval_adapter_returns_traceable_published_context(isolated_store, monkeypatch):
    from app.ai_learning import integration
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_ENABLED", "1")
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Index basics")
    version = service.add_content_version(lesson["id"], "# Indexes\n\nIndexes can improve query performance.")
    service.review_content(version["id"], "reviewed")
    service.publish_content(version["id"])
    context = integration.retrieve_context("indexes improve performance")
    assert len(context) == 1
    assert context[0]["course_title"] == "SQL Server"
    assert context[0]["lesson_title"] == "Index basics"
    assert context[0]["content_version_id"] == version["id"]
    assert context[0]["chunk_id"] > 0
    with connection() as conn:
        trace = conn.execute("SELECT query_sha256,result_chunk_ids_json,result_count FROM query_traces ORDER BY id DESC LIMIT 1").fetchone()
    assert trace["result_count"] == 1
    assert str(context[0]["chunk_id"]) in trace["result_chunk_ids_json"]
    assert "indexes improve performance" not in trace["query_sha256"]


def test_opt_in_retrieval_adapter_is_off_when_flag_is_disabled(isolated_store, monkeypatch):
    from app.ai_learning import integration
    monkeypatch.setenv("AI_LEARNING_ENABLED", "0")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_ENABLED", "1")
    assert integration.retrieve_context("anything") == []


def test_outline_parser_accepts_chatgpt_plain_topic_paragraphs(isolated_store):
    raw = """ChatGPT said:\\

\\

آشنایی با SQL Server 2025 و معماری کلی آن

\\

مفاهیم پایه پایگاه داده و مدل رابطه‌ای

\\

نصب و راه‌اندازی SQL Server

\\

مدیریت کاربران و مجوزهای دسترسی"""
    titles = service._parse_outline_titles(raw)
    assert titles == [
        "آشنایی با SQL Server 2025 و معماری کلی آن",
        "مفاهیم پایه پایگاه داده و مدل رابطه‌ای",
        "نصب و راه‌اندازی SQL Server",
        "مدیریت کاربران و مجوزهای دسترسی",
    ]


def test_outline_parser_still_rejects_unstructured_prose(isolated_store):
    assert service._parse_outline_titles("SQL Server is a database system.") == []


def test_learning_pause_state_is_persistent_and_resumable(isolated_store):
    paused = service.set_learning_paused(True, "test pause")
    assert paused["paused"] is True
    assert paused["reason"] == "test pause"
    assert service.get_learning_control()["paused"] is True
    resumed = service.set_learning_paused(False)
    assert resumed["paused"] is False
    assert resumed["reason"] == ""
