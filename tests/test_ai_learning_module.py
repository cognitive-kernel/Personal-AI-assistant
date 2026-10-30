from __future__ import annotations
import hashlib
import sqlite3
import pytest
from app.ai_learning import service
from app.ai_learning import browser_automation
from app.ai_learning.db import initialize_store, connection, StoreSchemaError

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "ai_learning.db"
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(path))
    initialize_store()
    return path


def test_complete_lesson_with_bibliography_needs_no_internal_status_marker():
    lesson = (
        "# Database indexing\n\n"
        + ("Indexes improve query performance by reducing the rows scanned for common lookups. " * 75)
        + "\n\n## منابع\n- Database System Concepts, Silberschatz, Korth, and Sudarshan"
    )
    assert browser_automation._lesson_has_terminal_bibliography(lesson)
    assert browser_automation._lesson_completion_status(lesson) == "COMPLETE"


def test_chatgpt_citation_hyperlinks_are_preserved_for_lesson_source_detection():
    from pathlib import Path

    answer = "# SQL indexes\n\nIndexes improve database query performance. Microsoft Learn explains the implementation."
    links = [
        {"title": "Microsoft Learn", "url": "https://learn.microsoft.com/sql/relational-databases/indexes/indexes"},
        {"title": "Microsoft Learn", "url": "https://learn.microsoft.com/sql/relational-databases/indexes/indexes"},
    ]

    enriched = browser_automation._append_captured_citation_links(answer, links)

    assert "https://learn.microsoft.com/sql/relational-databases/indexes/indexes" in enriched
    assert len(service._extract_references(enriched)) == 1
    assert enriched.count("https://learn.microsoft.com/sql/relational-databases/indexes/indexes") == 1
    assert 'citation_links = _capture_citation_links(page)' in Path(browser_automation.__file__).read_text(encoding="utf-8")

def test_generated_lesson_without_source_is_skipped_and_not_saved(isolated_store):
    course = service.create_course(title="Source-required lesson")
    topic = service.add_topic(course["id"], "Lesson without references")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="single", request_key="generated-lesson-no-source",
    )
    service.start_run(run["id"])
    response = (
        "# Lesson without references\n\n"
        + ("This is substantive lesson content, but it contains no source URL. " * 5)
    )
    result = service.complete_run(run["id"], response)
    assert result["status"] == "succeeded"
    assert service.list_lessons(topic["id"]) == []
    with connection() as conn:
        status = conn.execute(
            "SELECT status FROM run_topics WHERE run_id=? AND topic_id=?",
            (run["id"], topic["id"]),
        ).fetchone()["status"]
    assert status == "skipped"


def test_generated_lesson_with_textual_bibliography_is_saved(isolated_store, monkeypatch):
    course = service.create_course(title="Textual bibliography saves")
    topic = service.add_topic(course["id"], "Data structures")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="single", request_key="generated-lesson-textual-source",
    )
    service.start_run(run["id"])
    monkeypatch.setattr(service, "_find_semantic_duplicate_content", lambda *args, **kwargs: None)
    response = (
        "# Trees\n\n"
        + ("A tree is a hierarchical data structure used to represent relationships between objects. " * 5)
        + "\n\n## منابع\n- کتاب ساختمان داده، تننباوم و آگوستین"
    )
    result = service.complete_run(run["id"], response)
    assert result["status"] == "succeeded"
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    with connection() as conn:
        source = conn.execute(
            "SELECT title,url,source_type FROM sources WHERE content_version_id = "
            "(SELECT id FROM content_versions WHERE lesson_id = ? ORDER BY id DESC LIMIT 1)",
            (lessons[0]["id"],),
        ).fetchone()
    assert source is not None
    assert source["title"] == "کتاب ساختمان داده، تننباوم و آگوستین"
    assert source["url"] is None
    assert source["source_type"] == "textual_bibliography"


def test_generated_lesson_without_source_cannot_be_saved_or_reviewed(isolated_store, monkeypatch):
    course = service.create_course(title="No-source review guard")
    topic = service.add_topic(course["id"], "Generated lesson")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Generated lesson")
    monkeypatch.setattr(service, "_find_semantic_duplicate_content", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="منبع دارای پیوند"):
        service.add_content_version(
            lesson["id"],
            "# Generated lesson\n\nSubstantive content for this lesson.",
            source_kind="chatgpt_ui",
        )
    version = service.add_content_version(
        lesson["id"],
        "# Generated lesson\n\nSubstantive content for this lesson.",
    )
    with connection() as conn:
        conn.execute(
            "UPDATE content_versions SET source_kind='chatgpt_ui' WHERE id=?",
            (version["id"],),
        )
    with pytest.raises(ValueError, match="منبعی برای این درس"):
        service.review_content(version["id"], "reviewed")



def test_textual_book_reference_allows_generated_lesson_review_and_publish(isolated_store):
    course = service.create_course(title="Textual bibliography")
    topic = service.add_topic(course["id"], "Data structures")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Trees")
    markdown = """# Trees

A tree is a hierarchical data structure used to represent relationships.

## منابع
- کتاب ساختمان داده، تننباوم و آگوستین
"""
    version = service.add_content_version(lesson["id"], markdown)
    with connection() as conn:
        conn.execute(
            "UPDATE content_versions SET source_kind='chatgpt_ui' WHERE id=?",
            (version["id"],),
        )

    reviewed = service.review_content(version["id"], "reviewed")
    assert reviewed["review_status"] == "reviewed"
    published = service.publish_content(version["id"])
    assert published["review_status"] == "published"



def test_bulk_lesson_actions_never_delete_lessons_and_accept_textual_sources():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "ai_learning" / "static" / "app.js").read_text(encoding="utf-8")
    html = (root / "app" / "ai_learning" / "static" / "index.html").read_text(encoding="utf-8")

    assert "function hasTrailingSourceSection(markdown)" in script
    assert "کتابنامه" in script
    assert "const hasTextSource=hasTrailingSourceSection(markdown)" in script
    assert 'id="recover-deleted-lessons"' in html
    assert "بازیابی گروهی انتخاب‌شده‌ها" in script
    assert '$("recover-deleted-lessons").onclick=' in script

    approve_all_start = script.index('$("approve-all-lessons").onclick=')
    approve_all_end = script.index('$("publish-all-lessons").onclick=', approve_all_start)
    approve_all = script[approve_all_start:approve_all_end]
    assert "if(!quality.hasSource){skipped++;continue}" in approve_all
    assert "deleteLessonForBulk" not in approve_all

    publish_all_start = approve_all_end
    publish_all_end = script.index('$("reject-selected").onclick=', publish_all_start)
    publish_all = script[publish_all_start:publish_all_end]
    assert "if(!quality.hasSource){skipped++;continue}" in publish_all
    assert "deleteLessonForBulk" not in publish_all

    lesson_list_start = script.index("async function loadLessons(")
    lesson_list_end = script.index('$("show-all-lessons").onclick=', lesson_list_start)
    lesson_list = script[lesson_list_start:lesson_list_end]
    review_start = lesson_list.index('const approveBulk=el("button"')
    publish_start = lesson_list.index('const publishBulk=el("button"', review_start)
    delete_start = lesson_list.index('const deleteInvalidBulk=el("button"', publish_start)
    assert "deleteLessonForBulk" not in lesson_list[review_start:publish_start]
    assert "deleteLessonForBulk" not in lesson_list[publish_start:delete_start]

def test_soft_deleted_lesson_can_be_restored(isolated_store):
    course = service.create_course(title="Restore deleted lessons")
    topic = service.add_topic(course["id"], "Recovery")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Recoverable lesson")
    version = service.add_content_version(
        lesson["id"],
        "# Recoverable lesson\n\nThis lesson content must remain recoverable after a soft delete.",
    )
    service.review_content(version["id"], "reviewed")
    service.publish_content(version["id"])

    service.soft_delete_lesson(lesson["id"])
    deleted = service.list_deleted_lessons(course["id"])
    assert any(item["id"] == lesson["id"] for item in deleted)
    assert service.list_lessons(topic["id"]) == []
    updated_topic = service.get_course(course["id"])["topics"][0]
    assert updated_topic["processing_status"] == "pending"
    assert updated_topic["progress_percent"] == 0
    assert updated_topic["progress_label"] == "در انتظار دریافت درس"

    assert service.restore_soft_deleted_lesson(lesson["id"])["restored"] is True
    assert any(item["id"] == lesson["id"] for item in service.list_lessons(topic["id"]))
    with connection() as conn:
        active_chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE content_version_id=? AND active=1",
            (version["id"],),
        ).fetchone()[0]
    assert active_chunks > 0


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

def test_outline_import_creates_pending_topics_and_preserves_raw_response(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server 2005")
    monkeypatch.setattr(service, "_filter_duplicate_titles", lambda candidates, existing: (list(candidates), []))
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


def test_complete_outline_accepts_response_over_100k_and_preserves_all_topics(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server 2025")
    monkeypatch.setattr(service, "_filter_duplicate_titles", lambda candidates, existing: (list(candidates), []))
    run = service.create_run(course["id"], run_type="outline", request_key="large-outline-response")
    service.start_run(run["id"])
    titles = [f"سرفصل آزمایشی شماره {index:04d} با شرح تکمیلی و توضیحات بیشتر برای پوشش کامل موضوعات آموزشی" for index in range(1, 1401)]
    raw = "\n".join(f"{index}. {title}" for index, title in enumerate(titles, 1))
    raw += "\nAUDIT: status=COMPLETE; total_topics=1400; missing_domains=none; end_reason=all_applicable_domains_covered"
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

def test_run_lifecycle_imports_outline_without_auto_approval(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server")
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: [[float(i + 1), 1.0] for i, _ in enumerate(texts)])
    run = service.create_run(course["id"], run_type="outline", request_key="outline-test-1")
    assert run["status"] == "queued"
    assert "numbered topics" in run["prompt_text"]
    service.start_run(run["id"])
    finished = service.complete_run(run["id"], "1. Architecture\n2. Query processing\nAUDIT: status=COMPLETE; total_topics=2; missing_domains=none; end_reason=all_applicable_domains_covered")
    assert finished["status"] == "succeeded"
    assert len(finished["topics"]) == 2
    assert all(topic["approval_status"] == "pending" for topic in finished["topics"])


def test_lesson_run_creates_reviewable_version_not_published(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]], request_key="lesson-test-1")
    service.start_run(run["id"])
    finished = service.complete_run(run["id"], "# Index fundamentals\n\nIndexes speed up data access.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)")
    assert finished["status"] == "succeeded"
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    versions = service.list_versions(lessons[0]["id"])
    assert versions[0]["review_status"] == "needs_review"
    with connection() as conn:
        stored = conn.execute("SELECT run_id,raw_response FROM content_versions WHERE id=?", (versions[0]["id"],)).fetchone()
    assert stored["run_id"] == run["id"]
    assert stored["raw_response"] == "# Index fundamentals\n\nIndexes speed up data access.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"


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
    corrected = "[TOPIC_ID:" + str(a["id"]) + "] Index lesson\n\nIndex content explains lookup performance, index maintenance, and query-plan effects. A production database should compare execution plans, selectivity, write overhead, and storage costs before changing an index strategy. Monitor the workload and validate changes in a test environment.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)\n\n[TOPIC_ID:" + str(b["id"]) + "] Transaction lesson\n\nTransaction content explains isolation, commit, rollback, and consistency guarantees. A production workflow should handle errors, keep transactions short, and validate locking and concurrency behavior under representative load before deployment.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"

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


def test_save_run_partial_response_checkpoints_running_lesson(isolated_store):
    course = service.create_course(title="SQL Server", language="fa")
    topic = service.add_topic(course["id"], "Transactions")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        request_key="lesson-durable-checkpoint",
    )
    service.start_run(run["id"])
    partial = "# درس کامل\n\nبخش اول"
    saved = service.save_run_partial_response(run["id"], partial)
    assert saved["status"] == "running"
    assert saved["response_text"] == partial


def test_outline_and_lesson_prompts_enforce_configured_course_language(isolated_store):
    for language in ("fa", "en"):
        course = service.create_course(title="Language contract " + language, language=language)
        outline = service.create_run(
            course["id"], run_type="outline",
            request_key="language-outline-" + language,
        )
        assert "LANGUAGE OUTPUT CONTRACT (HARD REQUIREMENT)" in outline["prompt_text"]
        assert "Use the configured course language above for all natural-language output" in outline["prompt_text"]
        assert "Language: " + language + "." in outline["prompt_text"]

        topic = service.add_topic(course["id"], "Language-specific lesson")
        service.decide_topics([topic["id"]], "approved")
        lesson = service.create_run(
            course["id"], run_type="lesson", topic_ids=[topic["id"]],
            request_key="language-lesson-" + language,
        )
        assert "LANGUAGE OUTPUT CONTRACT (HARD REQUIREMENT)" in lesson["prompt_text"]
        assert (
            "including headings, explanations, examples, recap, exercises, and answer keys"
            in lesson["prompt_text"]
        )
        assert "Course language: " + language in lesson["prompt_text"]


def test_lesson_prompt_requires_bounded_parts_and_explicit_completion_marker(isolated_store):
    course = service.create_course(title="SQL Server 2025", language="fa")
    topic = service.add_topic(course["id"], "7. آشنایی با SQL و تفاوت‌های T-SQL")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        request_key="lesson-multipart-completion-contract",
    )
    assert "1200–1800 words per response part" in run["prompt_text"]
    assert "STAGE 1 — SCOPE ANALYSIS BEFORE WRITING" in run["prompt_text"]
    assert "target level, explicit or implied prerequisites" in run["prompt_text"]
    assert "do not use title length alone as the size signal" in run["prompt_text"]
    assert "The requested deliverable is always the complete lesson, never a short version" in run["prompt_text"]
    assert "Only after the complete lesson has been captured and saved may a separate optional summary be derived" in run["prompt_text"]
    assert "LESSON_STATUS: CONTINUE_REQUIRED" in run["prompt_text"]
    assert "LESSON_STATUS: COMPLETE" in run["prompt_text"]


def test_browser_lesson_completion_marker_helpers():
    assert browser_automation._lesson_completion_status(
        "## Part one\ncontent\nLESSON_STATUS: CONTINUE_REQUIRED; next_section=examples"
    ) == "CONTINUE_REQUIRED"
    assert browser_automation._lesson_completion_status(
        "## Complete lesson\ncontent\nLESSON_STATUS: COMPLETE"
    ) == "COMPLETE"
    assert browser_automation._lesson_completion_status("## Incomplete lesson\ncontent") is None
    assert browser_automation._lesson_completion_status(
        "## Lesson\nLESSON_STATUS: COMPLETE\nextra text after the marker"
    ) is None
    assert browser_automation._strip_lesson_status_markers(
        "## Lesson\ncontent\nLESSON_STATUS: COMPLETE"
    ) == "## Lesson\ncontent"



def test_short_lesson_marked_complete_is_continued_until_minimum_depth(monkeypatch):
    first = (
        "## مختصر\n\n"
        + "تعریف کوتاه. " * 20
        + "\nLESSON_STATUS: COMPLETE"
    )
    next_parts = [
        "## توضیح عمیق\n\n"
        + "شرح مفصل و نکته آموزشی. " * 300
        + "\nLESSON_STATUS: CONTINUE_REQUIRED; next_section=examples",
        "## مثال‌ها و عیب‌یابی\n\n"
        + "مثال کاربردی و توضیح مرحله‌ای. " * 650
        + "\nLESSON_STATUS: COMPLETE",
    ]
    prompts = []
    checkpoints = []

    monkeypatch.setattr(browser_automation, "_visible_response", lambda page: "last visible response")
    monkeypatch.setattr(browser_automation, "_assistant_message_count", lambda page: 1)
    monkeypatch.setattr(browser_automation, "_send_prompt", lambda page, prompt: prompts.append(prompt))

    def next_response(page, previous, cancel_event=None, previous_message_count=None):
        return next_parts.pop(0)

    monkeypatch.setattr(browser_automation, "_wait_for_complete_answer", next_response)
    completed = browser_automation._capture_lesson_until_complete(
        object(), first, on_checkpoint=checkpoints.append
    )

    assert len(prompts) == 2
    assert "claimed COMPLETE prematurely" in prompts[0]
    assert "at least 1200 words" in prompts[0]
    assert browser_automation._lesson_word_count(completed) >= 1200
    assert "LESSON_STATUS:" not in completed
    assert checkpoints[-1] == completed


def test_lesson_word_count_ignores_internal_completion_markers():
    short = "## Topic\n\n" + "some substantive word " * 30 + "\nLESSON_STATUS: COMPLETE"
    long = "## Topic\n\n" + "some substantive word " * 500 + "\nLESSON_STATUS: COMPLETE"
    assert browser_automation._lesson_word_count(short) < 1200
    assert browser_automation._lesson_word_count(long) >= 1200


def test_lesson_prompt_includes_course_context_and_specific_topic(isolated_store):
    course = service.create_course(title="SQL Server 2025", language="fa")
    topic = service.add_topic(
        course["id"], "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی",
        description="نقش منابع در معماری نرم‌افزار و تفاوت با موتور پایگاه داده محلی برنامه",
    )
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


def test_run_request_key_replays_after_completed_topics_are_filtered(isolated_store):
    course = service.create_course(title="SQL Server")
    completed_topic = service.add_topic(course["id"], "Transactions")
    pending_topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([completed_topic["id"], pending_topic["id"]], "approved")
    lesson = service.create_lesson(completed_topic["id"], "Existing transaction lesson")
    service.add_content_version(lesson["id"], "# Transactions\n\nSubstantive lesson content.")

    kwargs = {
        "run_type": "lesson",
        "topic_ids": [completed_topic["id"], pending_topic["id"]],
        "selection_mode": "selected",
        "request_key": "partial-topic-filter-key",
    }
    first = service.create_run(course["id"], **kwargs)
    second = service.create_run(course["id"], **kwargs)

    assert first["id"] == second["id"]
    assert [topic["topic_id"] for topic in first["topics"]] == [pending_topic["id"]]


def test_run_request_key_rejects_reuse_for_another_course(isolated_store):
    first_course = service.create_course(title="SQL Server")
    second_course = service.create_course(title="PostgreSQL")
    first = service.create_run(
        first_course["id"], run_type="outline", request_key="cross-course-key"
    )

    with pytest.raises(ValueError, match="different run request"):
        service.create_run(
            second_course["id"], run_type="outline", request_key="cross-course-key"
        )

    assert service.get_run(first["id"])["course_id"] == first_course["id"]


def test_run_request_key_rejects_reuse_for_different_topics(isolated_store):
    course = service.create_course(title="SQL Server")
    first_topic = service.add_topic(course["id"], "Transactions")
    second_topic = service.add_topic(course["id"], "Indexes")
    service.decide_topics([first_topic["id"], second_topic["id"]], "approved")
    first = service.create_run(
        course["id"], run_type="lesson", topic_ids=[first_topic["id"]],
        selection_mode="single", request_key="different-topic-key",
    )

    with pytest.raises(ValueError, match="different run request"):
        service.create_run(
            course["id"], run_type="lesson", topic_ids=[second_topic["id"]],
            selection_mode="single", request_key="different-topic-key",
        )

    assert [topic["topic_id"] for topic in service.get_run(first["id"])["topics"]] == [
        first_topic["id"]
    ]



def test_run_request_key_rejects_reuse_with_different_retry_policy(isolated_store):
    course = service.create_course(title="SQL Server")
    topic = service.add_topic(course["id"], "Transactions")
    service.decide_topics([topic["id"]], "approved")
    service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="single", request_key="retry-policy-key", force_retry=False,
    )

    with pytest.raises(ValueError, match="different run request"):
        service.create_run(
            course["id"], run_type="lesson", topic_ids=[topic["id"]],
            selection_mode="single", request_key="retry-policy-key", force_retry=True,
        )

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
    assert "restored_extra_tabs_closed" in automation
    assert "extra_page.close(run_before_unload=False)" in automation
    assert "api.openai.com" not in automation
    assert "context.cookies(" not in automation
    assert "storage_state(" not in automation
    assert "required_stability = 45.0 if stop_visible else _STABLE_SECONDS" in automation
    assert "_capture_lesson_until_complete" in automation
    assert "_MAX_LESSON_CONTINUATIONS = 8" in automation
    assert "lesson_continuation_limit" in automation
    assert "service.save_run_partial_response(" in automation
    assert "_worker_lease_kwargs" in automation
    assert "lesson_checkpoint_failed" in automation
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


def test_course_progress_keeps_fractional_percentage_for_large_courses(isolated_store):
    course = service.create_course(title="Large course progress")
    topics = [service.add_topic(course["id"], f"Topic {i}") for i in range(10)]
    service.decide_topics([topic["id"] for topic in topics], "approved")
    lesson = service.create_lesson(topics[0]["id"], "First lesson")
    service.add_content_version(lesson["id"], "Complete lesson content.")
    course_state = service.get_course(course["id"])
    assert course_state["progress_percent"] == 7.5
    assert course_state["topic_total"] == 10
    assert course_state["topic_completed"] == 0


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
    completed = service.complete_run(run["id"], "# Query optimization\n\nValid lesson content.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)")
    lessons = service.list_lessons(topic["id"])
    assert completed["status"] == "succeeded"
    assert len(lessons) == 1
    assert lessons[0]["progress_percent"] == 75
    versions = service.list_versions(lesson["id"])
    assert len(versions) == 2
    assert service.get_content_version(versions[0]["id"])["markdown"] == "# Query optimization\n\nValid lesson content.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"



def test_complete_run_uses_position_after_soft_deleted_lesson(isolated_store):
    course = service.create_course(title="Lesson position recovery")
    topic = service.add_topic(course["id"], "Recovery handling")
    service.decide_topics([topic["id"]], "approved")
    with connection() as conn:
        conn.execute(
            """INSERT INTO lessons(topic_id,position,title,status,deleted_at)
               VALUES(?,1,'Old deleted lesson','archived',CURRENT_TIMESTAMP)""",
            (topic["id"],),
        )

    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        request_key="lesson-position-soft-delete-collision",
    )
    service.start_run(run["id"])
    response = (
        "# Recovery handling\n\n"
        "This lesson explains how to recover a database workflow safely, preserve "
        "consistent state, validate the next step, and avoid writing over records "
        "that still participate in database uniqueness constraints.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"
    )
    completed = service.complete_run(run["id"], response)

    assert completed["status"] == "succeeded"
    lessons = service.list_lessons(topic["id"])
    assert len(lessons) == 1
    assert lessons[0]["position"] == 2


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
    topic = service.add_topic(
        course["id"], "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی",
        description="نقش منابع در معماری نرم‌افزار و تفاوت با موتور پایگاه داده محلی برنامه",
    )
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                             request_key="contextual-lesson-prompt")
    assert "Course / main subject: SQL Server 2025" in run["prompt_text"]
    assert "Target learning level: beginner_to_advanced" in run["prompt_text"]
    assert "نقش منابع در معماری نرم‌افزار و تفاوت با موتور پایگاه داده محلی برنامه" in run["prompt_text"]
    assert "Do not claim the application itself uses SQL Server" in run["prompt_text"]
    assert "134. مدیریت حافظه، پردازنده، ورودی‌ـ‌خروجی و منابع سیستمی" in run["prompt_text"]
    assert "not as a generic operating-system lesson" in run["prompt_text"]
    service.start_run(run["id"])
    answer = (
        "# مدیریت حافظه در SQL Server 2025\n\n"
        "در SQL Server 2025، مدیریت حافظه بخشی از کنترل منابع موتور پایگاه داده است. "
        "برای عیب‌یابی باید مصرف حافظه، فشار حافظه و اثر بار کاری را همراه با شاخص‌های سیستم بررسی کرد.\n\n"
        "## روش بررسی\n\n"
        "از نماهای مدیریتی و مستندات رسمی برای بررسی وضعیت استفاده کنید و پیش از تغییر تنظیمات، "
        "اثر تغییر را در محیط آزمایشی بسنجید.\n\nمنبع: [مستندات Microsoft SQL](https://learn.microsoft.com/sql/)"
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
        "memory pressure, and practical troubleshooting steps.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)",
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
                         "clerks and wait statistics, compare workload behavior, and validate changes safely.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)")
    retry = service.create_run(course["id"], run_type="lesson", topic_ids=[topic["id"]],
                               request_key="retry-lesson-version", force_retry=True)
    assert "[FORCE_RETRY_EXISTING_LESSON]" in retry["prompt_text"]
    service.start_run(retry["id"])
    service.complete_run(retry["id"],
                         "# Updated memory management\n\nSQL Server memory management must be assessed "
                         "against the current workload and available memory. Review memory grants, buffer pool "
                         "pressure, relevant performance counters, and query behavior before making a change.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)")
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



def test_chatgpt_lesson_is_saved_when_embedding_model_is_unavailable(isolated_store, monkeypatch):
    old = (
        "Database indexes improve lookup speed by locating matching records without "
        "scanning every row in the table."
    )
    _course_a, _topic_a, lesson_a = _create_approved_lesson("Database indexes")
    service.add_content_version(lesson_a["id"], old)

    course_b = service.create_course(title="Course transaction management")
    topic_b = service.add_topic(course_b["id"], "Transaction management")
    service.decide_topics([topic_b["id"]], "approved")
    run = service.create_run(
        course_b["id"], run_type="lesson", topic_ids=[topic_b["id"]],
        request_key="save-lesson-without-embedding-model",
    )
    service.start_run(run["id"])

    def unavailable_embed(texts, model=None):
        raise RuntimeError("embedding model not installed")

    monkeypatch.setattr(service, "ollama_embed", unavailable_embed)
    answer = (
        "# Transaction management in SQL Server 2025\n\n"
        "Transactions group database operations into a logical unit. SQL Server uses "
        "transaction boundaries to preserve consistency and supports COMMIT and ROLLBACK. "
        "Administrators should understand isolation levels, locking, deadlocks, and recovery.\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"
    )
    result = service.complete_run(run["id"], answer)

    assert result["status"] == "succeeded"
    assert service.list_lessons(topic_b["id"])
    saved = service.list_lessons(topic_b["id"])[0]
    versions = service.list_versions(saved["id"])
    assert versions
    assert "Transactions group database operations" in service.get_content_version(versions[0]["id"])["markdown"]

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
        f"[TOPIC_ID:{first_topic['id']}] Index basics\n\n{first_body}\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)\n\n"
        f"[TOPIC_ID:{second_topic['id']}] Faster lookups\n\n{second_body}\n\nSources: [Microsoft Learn](https://learn.microsoft.com/sql/)"
    )

    def fake_embed(texts, model=None):
        vectors = {
            first_body: [1.0, 0.0, 0.0],
            second_body: [0.98, 0.18, 0.0],
        }
        return [vectors[next(key for key in vectors if key in text)] for text in texts]

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


def test_selected_browser_lessons_are_persisted_as_single_topic_fifo_runs(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server 2025")
    topics = [service.add_topic(course["id"], f"Topic {index}") for index in range(3)]
    service.decide_topics([topic["id"] for topic in topics], "approved")
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)
    monkeypatch.setattr(service, "get_learning_control", lambda: {"paused": False})

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured["args"] = args
            captured["name"] = name
            captured["daemon"] = daemon
        def start(self):
            captured["started"] = True

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.start_lesson_run(
        course["id"], [topic["id"] for topic in topics], "selected-three-topics"
    )

    runs = service.list_course_runs(course["id"], 20)
    persisted = [service.get_run(item["id"]) for item in runs]
    persisted_topic_ids = {
        int(run["topics"][0]["topic_id"])
        for run in persisted
        if len(run["topics"]) == 1
    }
    assert result["status"] == "running"
    assert result["queued_topic_count"] == 3
    assert persisted_topic_ids == {int(topic["id"]) for topic in topics}
    assert sum(run["status"] == "running" for run in persisted) == 1
    assert sum(run["status"] == "queued" for run in persisted) == 2
    assert len(captured["args"]) == 4  # no in-memory follow-up topic list
    assert captured["started"] is True
    assert service.has_queued_run_for_course(course["id"]) is True
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)


def test_browser_lesson_retry_is_persisted_when_worker_is_active(isolated_store, monkeypatch):
    course = service.create_course(title="Retry while browser is busy")
    topic = service.add_topic(course["id"], "Memory management")
    service.decide_topics([topic["id"]], "approved")

    monkeypatch.setattr(browser_automation, "_ACTIVE", True)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", 456)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", course["id"])
    monkeypatch.setattr(service, "get_learning_control", lambda: {"paused": False})

    result = browser_automation.start_lesson_run(
        course["id"], [topic["id"]], "retry-while-active", force_retry=True
    )

    assert result["status"] == "queued"
    assert result["queued_topic_count"] == 1
    assert "پس از پایان اجرای فعلی" in result["message"]
    queued_run = service.get_run(result["run_id"])
    assert queued_run["status"] == "queued"
    assert [item["topic_id"] for item in queued_run["topics"]] == [topic["id"]]

def test_long_lesson_content_is_not_rejected_by_arbitrary_size_limit(isolated_store, monkeypatch):
    course = service.create_course(title="Long lesson course")
    topic = service.add_topic(course["id"], "Long lesson topic")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Long lesson")
    monkeypatch.setattr(service, "_find_semantic_duplicate_content", lambda *args, **kwargs: None)
    markdown = "# Long lesson\n\n" + ("Detailed instructional content. " * 20000)
    assert len(markdown) > 500_000
    version = service.add_content_version(lesson["id"], markdown, raw_response=markdown)
    assert len(version["markdown"]) == len(markdown)


def test_only_rejected_lesson_versions_can_be_deleted(isolated_store, monkeypatch):
    course = service.create_course(title="Rejected version deletion")
    topic = service.add_topic(course["id"], "Version deletion topic")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Version deletion lesson")
    monkeypatch.setattr(service, "_find_semantic_duplicate_content", lambda *args, **kwargs: None)
    rejected = service.add_content_version(lesson["id"], "# Bad version\n\nIncomplete response.")
    service.review_content(rejected["id"], "rejected")
    deleted = service.delete_rejected_content_version(rejected["id"])
    assert deleted["deleted"] is True
    assert service.list_versions(lesson["id"]) == []


def test_non_rejected_lesson_version_cannot_be_deleted(isolated_store, monkeypatch):
    course = service.create_course(title="Keep reviewed versions")
    topic = service.add_topic(course["id"], "Keep version topic")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Keep version lesson")
    monkeypatch.setattr(service, "_find_semantic_duplicate_content", lambda *args, **kwargs: None)
    version = service.add_content_version(lesson["id"], "# Valid version\n\nSubstantive lesson content.")
    with pytest.raises(ValueError, match="فقط نسخه"):
        service.delete_rejected_content_version(version["id"])


def test_response_capture_waits_longer_and_supports_continue_generation():
    from pathlib import Path
    source = Path(browser_automation.__file__).read_text(encoding="utf-8")
    assert "_RESPONSE_TIMEOUT_SECONDS = 1800" in source
    assert 'button:has-text("Continue generating")' in source
    assert "_STABLE_SECONDS = 20.0" in source
    assert "partial_response=last_text or None" in source
    assert "service.mark_run_needs_user_action(" in source
    assert "**_worker_lease_kwargs" in source


def test_needs_user_action_preserves_partial_provider_response(isolated_store):
    course = service.create_course(title="Partial response recovery")
    topic = service.add_topic(course["id"], "Long response topic")
    service.decide_topics([topic["id"]], "approved")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[topic["id"]],
        selection_mode="single", request_key="partial-response-preserved",
    )
    service.start_run(run["id"])
    partial = "# Long lesson\n\nFirst part of the lesson was received, but the answer stopped before completion."
    saved = service.mark_run_needs_user_action(
        run["id"], "chatgpt_response_timeout", "Timed out", partial
    )
    assert saved["status"] == "needs_user_action"
    assert saved["response_text"] == partial



def test_provider_network_error_is_actionable():
    error = browser_automation._provider_gate_error(
        "https://chatgpt.com/", "Network error. Trying to reconnect."
    )
    assert error is not None
    assert error.code == "chatgpt_network_error"
    assert "VPN" in str(error)


def test_selected_lesson_request_key_does_not_embed_all_topic_ids():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "app" / "static" / "ai-learning.js").read_text(encoding="utf-8")
    assert 'const requestKey = "ui-selected-lessons-" + selectedCourse + "-" + Date.now();' in script
    assert 'ids.join("-") + "-" + Date.now()' not in script

def test_review_and_publish_return_scroll_to_the_same_parent_topic():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "app" / "static" / "ai-learning.js").read_text(encoding="utf-8")
    assert 'card.id = "topic-card-" + topic.id;' in script
    assert 'function returnToTopic(topicId)' in script
    assert 'card.scrollIntoView({behavior:"smooth", block:"center"});' in script
    assert 'card.focus({preventScroll:true})' in script

    review_start = script.index('action("تأیید بازبینی"')
    review_end = script.index('action("رد نسخه"', review_start)
    review_handler = script[review_start:review_end]
    assert 'await showLessons(topic);' in review_handler
    assert 'returnToTopic(topic.id);' in review_handler
    assert review_handler.index('await showLessons(topic);') < review_handler.index('returnToTopic(topic.id);')

    publish_start = script.index('action("انتشار"')
    publish_end = script.index('if (version.review_status === "published")', publish_start)
    publish_handler = script[publish_start:publish_end]
    assert 'await showLessons(topic);' in publish_handler
    assert 'returnToTopic(topic.id);' in publish_handler
    assert publish_handler.index('await showLessons(topic);') < publish_handler.index('returnToTopic(topic.id);')

    decision_start = script.index("async function decide(topicId, decision)")
    decision_end = script.index("async function editTopic", decision_start)
    assert "returnToTopic(topicId);" in script[decision_start:decision_end]

    bulk_start = script.index("async function bulkReviewLessons(ids)")
    bulk_end = script.index('byId("approve-all-lessons")', bulk_start)
    bulk_handler = script[bulk_start:bulk_end]
    assert "returnToTopic(topic.id);" in bulk_handler

    manual_start = script.index("async function createLesson(topic)")
    manual_end = script.index("byId(\"course-form\")", manual_start)
    manual_handler = script[manual_start:manual_end]
    assert "returnToTopic(topic.id);" in manual_handler

def test_active_learning_ui_publish_returns_to_parent_topic():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "app" / "ai_learning" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'function returnToTopic(topicId)' in script
    assert 'const catalog=$("catalog-menu");if(catalog)catalog.open=true;' in script
    assert 'const lessonDetails=$("lesson-list-details");if(lessonDetails)lessonDetails.open=false;' in script
    assert 'card.open=true;' in script
    assert 'while(ancestor){ancestor.open=true;ancestor=ancestor.parentElement.closest("details.topic")}' in script
    assert 'card.scrollIntoView({behavior:"smooth",block:"start"});' in script

    publish_start = script.index('if(v.review_status==="reviewed"){const b=el("button","انتشار برای بازیابی")')
    publish_end = script.index('a.append(b)}', publish_start) + len('a.append(b)}')
    publish_handler = script[publish_start:publish_end]
    assert 'const parentTopicId=Number(l._topicId||topicId)' in publish_handler
    assert 'await loadLessons(parentTopicId,root,inline)' in publish_handler
    assert 'if(!inline)returnToTopic(parentTopicId)' in publish_handler
    assert 'else{root.hidden=true;root.replaceChildren();root.dataset.loaded="false"}' in publish_handler
    assert 'say(inline?"نسخه منتشر شد؛ پنل درس‌ها بسته شد.":"نسخه منتشر شد؛ به سرفصل مربوط برگشتید.")' in publish_handler
    assert publish_handler.index('await loadLessons(parentTopicId,root,inline)') < publish_handler.index('if(!inline)returnToTopic(parentTopicId)')
    
    topic_open_start = script.index('button("مشاهده درس‌ها و محتوا"')
    topic_open_end = script.index('button("تلاش مجدد دریافت/اصلاح درس"', topic_open_start)
    topic_open_handler = script[topic_open_start:topic_open_end]
    assert 'head.after(inlineLessons)' in topic_open_handler
    assert 'window.scrollTo(previousScrollX,previousScrollY)' in topic_open_handler


def test_outline_parser_keeps_subject_chapter_hierarchy():
    raw = (
        "1. مبحث: معماری SQL Server\n"
        "1.1 سرفصل: معماری داخلی\n"
        "1.2 سرفصل: فایل‌ها و صفحات داده\n"
        "2. مبحث: عملکرد و بهینه‌سازی\n"
        "2.1 سرفصل: شاخص‌ها و طرح‌های اجرا\n"
        "AUDIT: status=COMPLETE; total_topics=5; missing_domains=none; end_reason=all_applicable_domains_covered"
    )
    items = service._parse_outline_items(raw)
    assert [item["title"] for item in items] == [
        "معماری SQL Server",
        "معماری داخلی",
        "فایل‌ها و صفحات داده",
        "عملکرد و بهینه‌سازی",
        "شاخص‌ها و طرح‌های اجرا",
    ]
    assert [item["path"] for item in items] == [(1,), (1, 1), (1, 2), (2,), (2, 1)]


def test_manual_outline_import_preserves_parent_child_links(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server hierarchy")
    monkeypatch.setattr(service, "_filter_duplicate_titles", lambda candidates, existing: (list(candidates), []))
    raw = (
        "1. مبحث: معماری SQL Server\n"
        "1.1 سرفصل: معماری داخلی\n"
        "1.2 سرفصل: فایل‌ها و صفحات داده\n"
        "2. مبحث: عملکرد و بهینه‌سازی\n"
        "2.1 سرفصل: شاخص‌ها و طرح‌های اجرا"
    )
    imported = service.import_outline(course["id"], raw)
    assert imported["imported"] == 5
    by_title = {topic["title"]: topic for topic in service.get_course(course["id"])["topics"]}
    assert by_title["معماری SQL Server"]["parent_topic_id"] is None
    assert by_title["معماری داخلی"]["parent_topic_id"] == by_title["معماری SQL Server"]["id"]
    assert by_title["فایل‌ها و صفحات داده"]["parent_topic_id"] == by_title["معماری SQL Server"]["id"]
    assert by_title["عملکرد و بهینه‌سازی"]["parent_topic_id"] is None
    assert by_title["شاخص‌ها و طرح‌های اجرا"]["parent_topic_id"] == by_title["عملکرد و بهینه‌سازی"]["id"]


def test_generated_outline_completion_preserves_parent_child_links(isolated_store, monkeypatch):
    course = service.create_course(title="SQL Server hierarchy")
    monkeypatch.setattr(service, "_filter_duplicate_titles", lambda candidates, existing: (list(candidates), []))
    run = service.create_run(course["id"], run_type="outline", request_key="hierarchical-outline")
    service.start_run(run["id"])
    raw = (
        "1. مبحث: معماری SQL Server\n"
        "1.1 سرفصل: معماری داخلی\n"
        "1.2 سرفصل: فایل‌ها و صفحات داده\n"
        "2. مبحث: عملکرد و بهینه‌سازی\n"
        "2.1 سرفصل: شاخص‌ها و طرح‌های اجرا\n"
        "AUDIT: status=COMPLETE; total_topics=5; missing_domains=none; end_reason=all_applicable_domains_covered"
    )
    result = service.complete_run(run["id"], raw)
    assert result["status"] == "succeeded"
    by_title = {topic["title"]: topic for topic in service.get_course(course["id"])["topics"]}
    assert by_title["معماری داخلی"]["parent_topic_id"] == by_title["معماری SQL Server"]["id"]
    assert by_title["فایل‌ها و صفحات داده"]["parent_topic_id"] == by_title["معماری SQL Server"]["id"]
    assert by_title["شاخص‌ها و طرح‌های اجرا"]["parent_topic_id"] == by_title["عملکرد و بهینه‌سازی"]["id"]


def test_cancel_run_is_idempotent_after_it_is_already_cancelled(isolated_store):
    course = service.create_course(title="Cancel race")
    run = service.create_run(course["id"], run_type="outline", request_key="cancel-race")
    first = service.cancel_run(run["id"])
    second = service.cancel_run(run["id"])
    assert first["status"] == "cancelled"
    assert first["cancelled_by_request"] is True
    assert second["status"] == "cancelled"
    assert second["cancelled_by_request"] is False


def test_active_learning_topic_tree_shows_percentage_and_manual_parent_selector():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    script = (root / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert 'status+" · "+percent+"٪"' in script
    assert 'el("div",undefined,"topic-children")' in script
    assert 'function childTopics(topicId)' in script
    assert 'payload.parent_topic_id=payload.parent_topic_id?Number(payload.parent_topic_id):null' in script
    assert 'id="topic-parent-select"' in html


def test_existing_topic_can_be_reparented_and_cycles_are_rejected(isolated_store):
    course = service.create_course(title="Topic parent edits")
    root = service.add_topic(course["id"], "Main subject")
    child = service.add_topic(course["id"], "Child chapter")
    grandchild = service.add_topic(course["id"], "Nested chapter", parent_topic_id=child["id"])

    moved = service.edit_topic(child["id"], parent_topic_id=root["id"], update_parent=True)
    assert moved["parent_topic_id"] == root["id"]
    assert service.edit_topic(child["id"], title="Renamed chapter")["parent_topic_id"] == root["id"]

    with pytest.raises(ValueError, match="descendants"):
        service.edit_topic(root["id"], parent_topic_id=grandchild["id"], update_parent=True)

    promoted = service.edit_topic(child["id"], parent_topic_id=None, update_parent=True)
    assert promoted["parent_topic_id"] is None


def test_topic_edit_api_accepts_explicit_parent_clear_but_omission_preserves_parent():
    from app.ai_learning.routes import TopicEdit

    omitted = TopicEdit(title="Rename only")
    assert "parent_topic_id" not in omitted.model_fields_set
    clear = TopicEdit(parent_topic_id=None)
    assert "parent_topic_id" in clear.model_fields_set
    assert clear.parent_topic_id is None




def test_explicit_browser_lesson_retry_recovers_global_pause(isolated_store, monkeypatch):
    course = service.create_course(title="Retry after global pause")
    topic = service.add_topic(course["id"], "Interrupted topic")
    service.decide_topics([topic["id"]], "approved")

    state = {"paused": True}
    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)
    monkeypatch.setattr(service, "get_learning_control", lambda: dict(state))

    def set_paused(paused, reason=""):
        state["paused"] = paused
        return dict(state)

    monkeypatch.setattr(service, "set_learning_paused", set_paused)

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured["args"] = args
        def start(self):
            captured["started"] = True

    monkeypatch.setattr(browser_automation.threading, "Thread", FakeThread)
    result = browser_automation.start_lesson_run(
        course["id"], [topic["id"]], "retry-after-global-pause", force_retry=True
    )

    assert state["paused"] is False
    assert result["status"] == "running"
    assert captured["started"] is True
    assert service.get_run(result["run_id"])["status"] == "running"


def test_explicit_lesson_retry_recovers_global_pause_after_validating_topics(isolated_store, monkeypatch):
    course = service.create_course(title="Retry recovery from global pause")
    topic = service.add_topic(course["id"], "Memory management")
    service.decide_topics([topic["id"]], "approved")
    service.set_learning_paused(True, "simulated interruption")

    monkeypatch.setattr(browser_automation, "_ACTIVE", False)
    monkeypatch.setattr(browser_automation, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(browser_automation, "_ACTIVE_COURSE_ID", None)

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.started = False
        def start(self):
            self.started = True

    threads = []
    def fake_thread(**kwargs):
        thread = FakeThread(**kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(browser_automation.threading, "Thread", fake_thread)
    result = browser_automation.start_lesson_run(
        course["id"], [topic["id"]], "retry-after-global-pause", force_retry=True
    )

    assert result["status"] == "running"
    assert service.get_learning_control()["paused"] is False
    assert threads and threads[0].started is True
    browser_automation._ACTIVE = False
    browser_automation._ACTIVE_RUN_ID = None
    browser_automation._ACTIVE_COURSE_ID = None


def test_invalid_lesson_retry_does_not_clear_global_pause(isolated_store):
    course = service.create_course(title="Invalid retry preserves pause")
    service.set_learning_paused(True, "simulated interruption")

    with pytest.raises(ValueError, match="سرفصل انتخاب‌شده"):
        browser_automation.start_lesson_run(
            course["id"], [999999], "invalid-retry-keeps-pause", force_retry=True
        )

    assert service.get_learning_control()["paused"] is True
    service.set_learning_paused(False)

def test_ai_learning_retry_migration_adds_bounded_schedule_columns(isolated_store):
    with connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        control_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(ai_learning_control)").fetchall()
        }
        version = conn.execute("SELECT MAX(version) FROM ai_learning_migrations").fetchone()[0]
    assert {"automatic_retry_count", "next_retry_at"} <= columns
    assert {"retry_after", "retry_reason"} <= control_columns
    assert {"worker_lease_owner", "worker_lease_until", "worker_lease_epoch"} <= control_columns
    assert version == 7
