from __future__ import annotations
import pytest
from app.ai_learning import service
from app.ai_learning.db import initialize_store

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()

def test_version_detail_keeps_provenance(isolated_store):
    course=service.create_course(title="Python")
    topic=service.add_topic(course["id"],"Typing")
    service.decide_topics([topic["id"]],"approved")
    lesson=service.create_lesson(topic["id"],"Type hints")
    version=service.add_content_version(lesson["id"],"# Type hints\n\nAnnotations describe interfaces.",raw_response="raw answer")
    detail=service.get_content_version(version["id"])
    assert detail["raw_response"]=="raw answer"
    assert detail["course_title"]=="Python" and detail["topic_title"]=="Typing"

def test_lesson_title_edit_preserves_content_history(isolated_store):
    course=service.create_course(title="Python")
    topic=service.add_topic(course["id"],"Typing")
    service.decide_topics([topic["id"]],"approved")
    lesson=service.create_lesson(topic["id"],"Old")
    version=service.add_content_version(lesson["id"],"Stable content")
    assert service.edit_lesson(lesson["id"],"New")["title"]=="New"
    assert service.get_content_version(version["id"])["markdown"]=="Stable content"

def test_duplicate_batch_topic_marker_fails_without_partial_lessons(isolated_store):
    course=service.create_course(title="Databases")
    a=service.add_topic(course["id"],"Indexes");b=service.add_topic(course["id"],"Transactions")
    service.decide_topics([a["id"],b["id"]],"approved")
    run=service.create_run(course["id"],run_type="lesson",topic_ids=[a["id"],b["id"]],selection_mode="selected",request_key="duplicate-marker")
    service.start_run(run["id"])
    response=f"[TOPIC_ID:{a['id']}] Index lesson\n\nIndex content.\n[TOPIC_ID:{a['id']}] Duplicate lesson\n\nDuplicate content.\n[TOPIC_ID:{b['id']}] Transaction lesson\n\nTransaction content."
    result=service.complete_run(run["id"],response)
    assert result["status"]=="needs_user_action"
    assert service.list_lessons(a["id"])==[] and service.list_lessons(b["id"])==[]


def test_course_run_history_and_outline_recovery(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    run = service.create_run(course["id"], run_type="outline", request_key="outline-recovery")
    service.start_run(run["id"])
    failed = service.complete_run(run["id"], "This response has no parseable outline items.")
    assert failed["status"] == "needs_user_action"
    history = service.list_course_runs(course["id"])
    assert history[0]["id"] == run["id"]
    assert history[0]["status"] == "needs_user_action"
    recovered = service.complete_run(run["id"], "1. Fundamentals\n2. Installation\n3. Security")
    assert recovered["status"] == "succeeded"
    assert [topic["title"] for topic in service.get_course(course["id"])["topics"]] == [
        "Fundamentals", "Installation", "Security"
    ]


def test_new_topic_position_skips_soft_deleted_positions(isolated_store):
    course = service.create_course(title="Databases")
    first = service.add_topic(course["id"], "First")
    second = service.add_topic(course["id"], "Second")
    service.soft_delete_topic(second["id"])
    third = service.add_topic(course["id"], "Third")
    assert first["position"] == 1
    assert third["position"] == 3


def test_browser_timeout_is_recoverable(isolated_store):
    course = service.create_course(title="SQL Server")
    run = service.create_run(course["id"], run_type="outline", request_key="browser-timeout")
    service.start_run(run["id"])
    marked = service.mark_run_needs_user_action(run["id"], "response_timeout", "Timed out waiting for ChatGPT.")
    assert marked["status"] == "needs_user_action"
    recovered = service.complete_run(run["id"], "1. Basics\n2. Query processing")
    assert recovered["status"] == "succeeded"


def test_outline_completion_skips_existing_topic_titles(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    existing = service.add_topic(course["id"], "Basics")
    run = service.create_run(course["id"], run_type="outline", request_key="dedupe-outline")
    service.start_run(run["id"])
    completed = service.complete_run(run["id"], "1. Basics\n2. Installation\n3.  Query processing ")
    titles = [topic["title"] for topic in service.get_course(course["id"])["topics"]]
    assert completed["status"] == "succeeded"
    assert titles.count("Basics") == 1
    assert "Installation" in titles
    assert "Query processing" in titles


def test_outline_import_skips_existing_topics(isolated_store):
    course = service.create_course(title="SQL Server 2025")
    service.add_topic(course["id"], "Basics")
    imported = service.import_outline(course["id"], "1. Basics\n2. Indexes")
    titles = [topic["title"] for topic in service.get_course(course["id"])["topics"]]
    assert imported["imported"] == 1
    assert imported["skipped_existing"] == 1
    assert titles.count("Basics") == 1
    assert "Indexes" in titles


def test_lesson_run_excludes_topics_with_existing_lessons(isolated_store):
    course = service.create_course(title="Databases")
    done = service.add_topic(course["id"], "Basics")
    todo = service.add_topic(course["id"], "Indexes")
    service.decide_topics([done["id"], todo["id"]], "approved")
    service.create_lesson(done["id"], "Existing lesson")
    run = service.create_run(
        course["id"], run_type="lesson", topic_ids=[done["id"], todo["id"]],
        selection_mode="selected", request_key="skip-existing-lessons"
    )
    assert [topic["topic_id"] for topic in run["topics"]] == [todo["id"]]
    with pytest.raises(ValueError, match="already have lessons"):
        service.create_run(
            course["id"], run_type="lesson", topic_ids=[done["id"]],
            selection_mode="single", request_key="skip-only-existing"
        )



def test_bulk_delete_course_run_history_keeps_unselected_runs(isolated_store):
    course = service.create_course(title="SQL Server")
    run_ids = []
    for key in ("bulk-delete-a", "bulk-delete-b", "bulk-delete-keep"):
        run = service.create_run(course["id"], run_type="outline", request_key=key)
        service.start_run(run["id"])
        service.complete_run(run["id"], "1. Fundamentals\n2. Query processing")
        run_ids.append(run["id"])
    result = service.delete_course_runs(course["id"], run_ids[:2])
    remaining = service.list_course_runs(course["id"], limit=10)
    assert result["deleted"] == 2
    assert {item["id"] for item in remaining} == {run_ids[2]}


def test_bulk_delete_course_runs_refuses_active_runs(isolated_store):
    course = service.create_course(title="SQL Server")
    run = service.create_run(course["id"], run_type="outline", request_key="active-run")
    service.start_run(run["id"])
    with pytest.raises(ValueError, match="Active runs cannot be deleted"):
        service.delete_course_runs(course["id"], [run["id"]])
