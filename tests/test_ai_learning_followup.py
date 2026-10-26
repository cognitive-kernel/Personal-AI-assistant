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
