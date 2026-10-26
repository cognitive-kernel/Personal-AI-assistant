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

def test_bulk_topic_actions_support_more_than_500_topics(isolated_store):
    course = service.create_course(title="Large course")
    with connection() as conn:
        conn.executemany(
            "INSERT INTO topics(course_id,position,title) VALUES(?,?,?)",
            [(course["id"], index, f"Topic number {index}") for index in range(1, 1201)],
        )
        topic_ids = [int(row["id"]) for row in conn.execute(
            "SELECT id FROM topics WHERE course_id=? ORDER BY position", (course["id"],)
        ).fetchall()]

    reversed_ids = list(reversed(topic_ids))
    ordered = service.reorder_topics(course["id"], reversed_ids)
    assert len(ordered) == 1200
    assert ordered[0]["id"] == reversed_ids[0]
    assert ordered[-1]["id"] == reversed_ids[-1]

    assert service.decide_topics(topic_ids, "approved") == 1200
    with connection() as conn:
        approved = conn.execute(
            "SELECT COUNT(*) FROM topics WHERE course_id=? AND approval_status='approved'",
            (course["id"],),
        ).fetchone()[0]
    assert approved == 1200

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



def test_outline_parser_accepts_plain_lines_and_ignores_chatgpt_wrapper_and_audit():
    raw = (
        "ChatGPT said:\n"
        "تفاوت Identity با Sequence و مدیریت تخصیص شناسه\n"
        "ایجاد و مدیریت Sequence با CREATE SEQUENCE\n"
        "مدیریت تراکنش‌ها و کنترل هم‌زمانی\n"
        "AUDIT: status=COMPLETE; total_topics=1400; missing_domains=none; end_reason=all_applicable_domains_covered"
    )
    assert service._parse_outline_titles(raw) == [
        "تفاوت Identity با Sequence و مدیریت تخصیص شناسه",
        "ایجاد و مدیریت Sequence با CREATE SEQUENCE",
        "مدیریت تراکنش‌ها و کنترل هم‌زمانی",
    ]


def test_outline_parser_does_not_truncate_large_plain_line_outlines():
    raw = "\n".join(f"سرفصل آزمایشی شماره {index}" for index in range(1, 1401))
    titles = service._parse_outline_titles(raw)
    assert len(titles) == 1400
    assert titles[0] == "سرفصل آزمایشی شماره 1"
    assert titles[-1] == "سرفصل آزمایشی شماره 1400"


def test_outline_import_accepts_plain_line_outline_and_preserves_raw_response(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server 2025")
    raw = (
        "ChatGPT said:\n"
        "تفاوت Identity با Sequence و مدیریت تخصیص شناسه\n"
        "ایجاد و مدیریت Sequence با CREATE SEQUENCE\n"
        "مدیریت تراکنش‌ها و کنترل هم‌زمانی\n"
        "AUDIT: status=COMPLETE; total_topics=3; missing_domains=none; end_reason=all_applicable_domains_covered"
    )
    monkeypatch.setattr(service, "_filter_duplicate_titles", lambda candidates, existing: (list(candidates), []))
    result = service.import_outline(course["id"], raw)
    assert result["imported"] == 3
    assert [item["title"] for item in result["topics"]] == [
        "تفاوت Identity با Sequence و مدیریت تخصیص شناسه",
        "ایجاد و مدیریت Sequence با CREATE SEQUENCE",
        "مدیریت تراکنش‌ها و کنترل هم‌زمانی",
    ]
    with connection() as conn:
        saved = conn.execute("SELECT response_text FROM runs WHERE id=?", (result["run_id"],)).fetchone()
    assert saved["response_text"] == raw


def test_complete_outline_accepts_response_over_100k_and_preserves_all_topics(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    run = service.create_run(course["id"], run_type="outline", request_key="large-outline-response")
    service.start_run(run["id"])
    titles = [f"سرفصل آزمایشی شماره {index:04d} با شرح تکمیلی" for index in range(1, 1401)]
    raw = "\n".join(titles)
    assert len(raw) > 100_000
    result = service.complete_run(run["id"], raw)
    assert result["status"] == "succeeded"
    assert len(result["topics"]) == 1400
    assert result["topics"][0]["title"] == titles[0]
    assert result["topics"][-1]["title"] == titles[-1]

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


def test_batch_run_requires_topic_markers_and_can_retry_mapping(isolated_store, monkeypatch):
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
    corrected = "[TOPIC_ID:" + str(a["id"]) + "] Index lesson\n\nIndex content explains lookup performance, index maintenance, and query-plan effects. A production database should compare execution plans, selectivity, write overhead, and storage costs before changing an index strategy. Monitor the workload and validate changes in a test environment.\n\n[TOPIC_ID:" + str(b["id"]) + "] Transaction lesson\n\nTransaction content explains isolation, commit, rollback, and consistency guarantees. A production workflow should handle errors, keep transactions short, and validate locking and concurrency behavior under representative load before deployment."

    def fake_embed(texts, model=None):
        return [
            [1.0, 0.0, 0.0] if "index maintenance" in text
            else [0.0, 1.0, 0.0]
            for text in texts
        ]

    monkeypatch.setattr(service, "ollama_embed", fake_embed)
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


def test_lesson_prompt_includes_course_context_and_specific_topic(isolated_store):
    course = service.create_course(title="SQL Server 2025", language="fa")
    topic = service.add_topic(course["id"], "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        request_key="course-context-lesson-prompt",
    )
    prompt = run["prompt_text"]
    assert "Course / main subject: SQL Server 2025" in prompt
    assert "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی" in prompt
    assert "in the context of SQL Server 2025" in prompt
    assert "never an empty response or title-only lesson" in prompt


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


def test_topic_and_lesson_pause_resume_and_progress_metrics(isolated_store):
    course = service.create_course(title="SQL Server progress")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    assert service.get_course(course["id"])["topics"][0]["progress_percent"] == 0

    paused_topic = service.set_topic_paused(topic["id"], True)
    assert paused_topic["processing_status"] == "paused"
    assert service.get_course(course["id"])["topics"][0]["progress_label"] == "متوقف‌شده"
    resumed_topic = service.set_topic_paused(topic["id"], False)
    assert resumed_topic["processing_status"] == "pending"

    lesson = service.create_lesson(topic["id"], "Index fundamentals")
    assert service.list_lessons(topic["id"])[0]["progress_percent"] == 25
    service.set_lesson_paused(lesson["id"], True)
    assert service.list_lessons(topic["id"])[0]["status"] == "paused"
    service.set_lesson_paused(lesson["id"], False)
    assert service.list_lessons(topic["id"])[0]["status"] == "draft"

    version = service.add_content_version(lesson["id"], "Indexes organize lookup paths.")
    assert service.list_lessons(topic["id"])[0]["progress_percent"] == 75
    assert service.get_course(course["id"])["progress_percent"] == 75


def test_resume_reuses_lesson_shell_with_empty_content(isolated_store):
    course = service.create_course(title="SQL Server recovery")
    topic = service.add_topic(course["id"], "Query optimization")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Query optimization")
    with connection() as conn:
        conn.execute(
            """INSERT INTO content_versions(lesson_id,version_no,markdown,raw_response,content_sha256,
               review_status,source_kind,provider) VALUES(?,1,'','','','published','chatgpt_ui','ChatGPT')""",
            (lesson["id"],),
        )
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="retry-empty-content")
    service.start_run(run["id"])
    completed = service.complete_run(run["id"], "# Query optimization\n\nValid lesson content.")
    lessons = service.list_lessons(topic["id"])
    assert completed["status"] == "succeeded"
    assert len(lessons) == 1
    assert lessons[0]["progress_percent"] == 75
    versions = service.list_versions(lesson["id"])
    assert len(versions) == 2
    assert service.get_content_version(versions[0]["id"])["markdown"] == "# Query optimization\n\nValid lesson content."


def test_published_version_can_be_withdrawn_and_review_reset(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Transactions")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Transaction basics")
    version = service.add_content_version(lesson["id"], "Transactions group database changes.")
    service.review_content(version["id"], "reviewed")
    service.publish_content(version["id"])
    withdrawn = service.unpublish_content(version["id"])
    assert withdrawn["review_status"] == "reviewed"
    with connection() as conn:
        active_chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE content_version_id=? AND active=1", (version["id"],)
        ).fetchone()[0]
    assert active_chunks == 0
    reset = service.review_content(version["id"], "needs_review")
    assert reset["review_status"] == "needs_review"


def test_empty_content_cannot_be_published(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Index basics")
    version = service.add_content_version(lesson["id"], "Temporary content")
    service.review_content(version["id"], "reviewed")
    with connection() as conn:
        conn.execute("UPDATE content_versions SET markdown='   ' WHERE id=?", (version["id"],))
    with pytest.raises(ValueError, match="متن درس خالی است"):
        service.publish_content(version["id"])

def test_empty_or_title_only_content_cannot_be_approved(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Index basics")
    empty = service.add_content_version(lesson["id"], "# Index basics")
    with pytest.raises(ValueError, match="متن درس خالی یا ناقص است"):
        service.review_content(empty["id"], "reviewed")
    assert service.get_content_version(empty["id"])["review_status"] == "needs_review"



def test_chatgpt_sources_are_persisted_with_content_versions(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    topic = service.add_topic(course["id"], "Memory management")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Memory management in SQL Server")
    version = service.add_content_version(
        lesson["id"],
        "Lesson body.\n\n## منابع\n[Microsoft Learn](https://learn.microsoft.com/sql/)\n"
        "Additional reference: https://learn.microsoft.com/sql/relational-databases/",
        raw_response="ChatGPT answer with https://learn.microsoft.com/sql/",
        source_kind="chatgpt_ui",
        provider="ChatGPT",
    )
    sources = service.list_content_sources(version["id"])
    assert len(sources) == 2
    assert all(source["validation_status"] == "unverified" for source in sources)
    assert any(source["title"] == "Microsoft Learn" for source in sources)
    assert all(source["content_version_id"] == version["id"] for source in sources)


def test_source_extraction_deduplicates_urls():
    text = "[Microsoft](https://learn.microsoft.com/sql/) and https://learn.microsoft.com/sql/"
    sources = service._extract_references(text)
    assert len(sources) == 1
    assert sources[0]["url"] == "https://learn.microsoft.com/sql/"



def test_active_learning_run_blocks_destructive_topic_deletion(isolated_store):
    course = service.create_course(title="SQL Server concurrency")
    topic = service.add_topic(course["id"], "Resource governance")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="active-run-delete-guard")
    service.start_run(run["id"])
    with pytest.raises(ValueError, match="اجرای فعال"):
        service.soft_delete_topic(topic["id"])



def test_title_only_chatgpt_response_is_not_saved_as_lesson_content(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    topic = service.add_topic(course["id"], "مدیریت حافظه و منابع سیستمی")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="title-only-lesson")
    service.start_run(run["id"])
    result = service.complete_run(run["id"], "# مدیریت حافظه و منابع سیستمی")
    assert result["status"] == "needs_user_action"
    assert result["error_code"] == "lesson_content_empty"
    assert service.list_lessons(topic["id"]) == []
    current_topic = service.get_course(course["id"])["topics"][0]
    assert current_topic["progress_percent"] == 0
    assert "دریافت ناموفق" in current_topic["progress_label"]


def test_lesson_capture_requires_body_and_preserves_course_context(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    topic = service.add_topic(course["id"], "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="contextual-lesson-prompt")
    assert "Course / main subject: SQL Server 2025" in run["prompt_text"]
    assert "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی" in run["prompt_text"]
    assert "not as a generic operating-system lesson" in run["prompt_text"]
    service.start_run(run["id"])
    answer = (
        "# مدیریت حافظه در SQL Server 2025\n\n"
        "در SQL Server 2025، مدیریت حافظه بخشی از کنترل منابع موتور پایگاه داده است. "
        "برای عیب‌یابی باید مصرف حافظه، فشار حافظه و اثر بار کاری را همراه با شاخص‌های سیستم بررسی کرد.\n\n"
        "## روش بررسی\n\n"
        "از نماهای مدیریتی و مستندات رسمی برای بررسی وضعیت استفاده کنید و پیش از تغییر تنظیمات، "
        "اثر تغییر را در محیط آزمایشی بسنجید."
    )
    result = service.complete_run(run["id"], answer)
    assert result["status"] == "succeeded"
    lesson = service.list_lessons(topic["id"])[0]
    version = service.get_content_version(service.list_versions(lesson["id"])[0]["id"])
    assert len(version["markdown"]) > 100
    assert "مدیریت حافظه" in version["markdown"]



def test_lesson_progress_uses_latest_version_even_if_an_older_version_was_published(isolated_store):
    course = service.create_course(title="SQL Server latest-version progress")
    topic = service.add_topic(course["id"], "Memory management")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Memory management")
    valid = service.add_content_version(lesson["id"], "Substantive lesson content with enough detail.")
    service.review_content(valid["id"], "reviewed")
    service.publish_content(valid["id"])
    # Simulate a legacy/corrupt empty version that was incorrectly marked published.
    with connection() as conn:
        conn.execute(
            """INSERT INTO content_versions(lesson_id,version_no,markdown,raw_response,content_sha256,
               review_status,source_kind,provider) VALUES(?,2,'','','','published','chatgpt_ui','ChatGPT')""",
            (lesson["id"],),
        )
    latest = service.list_lessons(topic["id"])[0]
    assert latest["progress_percent"] == 25
    assert "آخرین نسخه خالی است" in latest["progress_label"]

    # Retry should select the topic and reuse its lesson shell instead of duplicating it.
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="retry-after-empty-latest")
    service.start_run(run["id"])
    recovered = service.complete_run(
        run["id"],
        "# Memory management in SQL Server 2025\n\n"
        "This is a substantive lesson explaining SQL Server memory management, "
        "memory pressure, and practical troubleshooting steps.",
    )
    assert recovered["status"] == "succeeded"
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    assert lessons[0]["progress_percent"] == 75



def test_force_retry_keeps_existing_lesson_and_creates_a_new_version(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    topic = service.add_topic(course["id"], "Memory management")
    service.decide_topics([topic["id"]], "approved")
    first = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                              request_key="first-lesson-version")
    service.start_run(first["id"])
    service.complete_run(first["id"],
                         "# Memory management\n\nSQL Server memory management coordinates memory grants, "
                         "buffer pool usage, and workload pressure. Administrators should monitor memory "
                         "clerks and wait statistics, compare workload behavior, and validate changes safely.")
    retry = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                               request_key="retry-lesson-version", force_retry=True)
    assert "[FORCE_RETRY_EXISTING_LESSON]" in retry["prompt_text"]
    service.start_run(retry["id"])
    service.complete_run(retry["id"],
                         "# Updated memory management\n\nSQL Server memory management must be assessed "
                         "against the current workload and available memory. Review memory grants, buffer pool "
                         "pressure, relevant performance counters, and query behavior before making a change.")
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    versions = service.list_versions(lessons[0]["id"])
    assert len(versions) == 2
    assert service.get_content_version(versions[0]["id"])["markdown"].startswith("# Updated memory management")


def test_extract_references_keeps_clickable_markdown_urls(isolated_store):
    refs = service._extract_references(
        "منابع: [SQL Server documentation](https://learn.microsoft.com/sql/)"
    )
    assert refs == [{"title": "SQL Server documentation", "url": "https://learn.microsoft.com/sql/"}]



def _create_approved_lesson(title: str):
    course = service.create_course(title=f"Course {title}")
    topic = service.add_topic(course["id"], title)
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], title)
    return course, topic, lesson


def test_semantic_duplicate_content_is_rejected_across_different_lessons(
    isolated_store, monkeypatch
):
    old = (
        "A database index improves lookup speed by allowing the database to locate rows "
        "without scanning every record. It provides a structure that helps the engine find "
        "matching data efficiently."
    )
    paraphrase = (
        "Indexes make database searches faster because the engine can find matching records "
        "directly instead of reading every row in the table. This structure improves lookup "
        "efficiency."
    )
    course, topic_a, lesson_a = _create_approved_lesson("Database indexes")
    version_a = service.add_content_version(lesson_a["id"], old)
    _course_b, topic_b, lesson_b = _create_approved_lesson("Database lookup performance")

    def fake_embed(texts, model=None):
        vectors = {
            old: [1.0, 0.0, 0.0],
            paraphrase: [0.98, 0.18, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(service, "ollama_embed", fake_embed)
    with pytest.raises(ValueError, match="بسیار مشابه"):
        service.add_content_version(lesson_b["id"], paraphrase)

    with connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM content_versions WHERE lesson_id=?", (lesson_b["id"],)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM content_versions WHERE lesson_id=?", (lesson_a["id"],)
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM semantic_embedding_cache").fetchone()[0] == 2


def test_lesson_update_can_create_new_version_without_self_duplicate_rejection(isolated_store):
    _course, _topic, lesson = _create_approved_lesson("Python functions")
    first = service.add_content_version(
        lesson["id"],
        "A Python function packages reusable instructions and can accept parameters.",
    )
    second = service.add_content_version(
        lesson["id"],
        "Python functions group reusable instructions and may receive input parameters.",
    )
    assert second["version_no"] == first["version_no"] + 1


def test_chatgpt_capture_skips_semantically_duplicate_lesson(isolated_store, monkeypatch):
    old = (
        "A database index improves lookup speed by allowing the database to locate rows "
        "without scanning every record. It provides a structure that helps the engine find "
        "matching data efficiently."
    )
    paraphrase = (
        "# Faster database searches\n\nIndexes make database searches faster because the engine "
        "can find matching records directly instead of reading every row in the table. "
        "This structure improves lookup efficiency."
    )
    course, topic_a, lesson_a = _create_approved_lesson("Database indexes")
    service.add_content_version(lesson_a["id"], old)
    _course_b = service.create_course(title="Course lookup performance")
    topic_b = service.add_topic(_course_b["id"], "Database lookup performance")
    service.decide_topics([topic_b["id"]], "approved")
    run = service.create_run(
        _course_b["id"],
        run_type="lesson",
        topic_ids=[topic_b["id"]],
        selection_mode="single",
        request_key="semantic-duplicate-capture",
    )

    def fake_embed(texts, model=None):
        vectors = {
            old: [1.0, 0.0, 0.0],
            paraphrase: [0.98, 0.18, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(service, "ollama_embed", fake_embed)
    completed = service.complete_run(run["id"], paraphrase)
    assert completed["status"] == "succeeded"
    with connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE topic_id=?", (topic_b["id"],)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM run_topics WHERE run_id=? AND topic_id=?",
            (run["id"], topic_b["id"]),
        ).fetchone()[0] == "skipped"
        assert conn.execute(
            "SELECT processing_status FROM topics WHERE id=?", (topic_b["id"],)
        ).fetchone()[0] == "skipped"



def test_semantic_duplicates_inside_one_batch_are_saved_only_once(isolated_store, monkeypatch):
    course = service.create_course(title="Database course")
    first_topic = service.add_topic(course["id"], "Indexes")
    second_topic = service.add_topic(course["id"], "Lookup acceleration")
    service.decide_topics([first_topic["id"], second_topic["id"]], "approved")
    run = service.create_run(
        course["id"],
        run_type="lesson",
        topic_ids=[first_topic["id"], second_topic["id"]],
        selection_mode="selected",
        request_key="semantic-duplicate-batch",
    )
    first_body = (
        "A database index improves lookup speed by locating matching records without scanning "
        "every row. It uses an additional data structure to make searches more efficient."
    )
    second_body = (
        "Indexes make database searches faster by finding matching records directly instead of "
        "scanning the entire table. The extra structure improves lookup efficiency."
    )
    response = (
        f"[TOPIC_ID:{first_topic['id']}] Index basics\n\n{first_body}\n\n"
        f"[TOPIC_ID:{second_topic['id']}] Faster lookups\n\n{second_body}"
    )

    def fake_embed(texts, model=None):
        vectors = {
            first_body: [1.0, 0.0, 0.0],
            second_body: [0.98, 0.18, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(service, "ollama_embed", fake_embed)
    completed = service.complete_run(run["id"], response)
    assert completed["status"] == "succeeded"

    with connection() as conn:
        first_count = conn.execute(
            """SELECT COUNT(*) FROM lessons l JOIN content_versions cv ON cv.lesson_id=l.id
               WHERE l.topic_id=?""",
            (first_topic["id"],),
        ).fetchone()[0]
        second_count = conn.execute(
            """SELECT COUNT(*) FROM lessons l JOIN content_versions cv ON cv.lesson_id=l.id
               WHERE l.topic_id=?""",
            (second_topic["id"],),
        ).fetchone()[0]
        statuses = dict(conn.execute(
            "SELECT topic_id,status FROM run_topics WHERE run_id=?", (run["id"],)
        ).fetchall())
    assert first_count == 1
    assert second_count == 0
    assert statuses[first_topic["id"]] == "parsed"
    assert statuses[second_topic["id"]] == "skipped"



def test_semantic_dedup_does_not_reject_a_different_lesson(isolated_store, monkeypatch):
    old = (
        "A database index improves lookup speed by locating matching records without scanning "
        "every row. It adds a structure that makes searches more efficient."
    )
    different = (
        "Database transactions group operations into an atomic unit, preserve consistency, and "
        "control concurrent changes using isolation and rollback."
    )
    _course_a, _topic_a, lesson_a = _create_approved_lesson("Database indexes")
    service.add_content_version(lesson_a["id"], old)
    _course_b, _topic_b, lesson_b = _create_approved_lesson("Database transactions")

    def fake_embed(texts, model=None):
        vectors = {
            old: [1.0, 0.0, 0.0],
            different: [0.0, 1.0, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(service, "ollama_embed", fake_embed)
    version = service.add_content_version(lesson_b["id"], different)
    assert version["version_no"] == 1
