"""Loopback-only API for the optional AI-learning module."""
from __future__ import annotations
from typing import Any, Literal
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from . import config, service
from .db import StoreSchemaError, initialize_store
from ..task_routes import require_loopback_client

router = APIRouter(prefix="/api/ai-learning", tags=["ai-learning"], dependencies=[Depends(require_loopback_client)])

class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    provider: str = Field(default="manual", min_length=1, max_length=80)
    language: str = Field(default="fa", min_length=2, max_length=20)
    target_level: str = Field(default="beginner_to_advanced", min_length=1, max_length=80)
    scope_notes: str = Field(default="", max_length=4000)
    model_config = ConfigDict(extra="forbid")

class RunCreate(BaseModel):
    course_id: int = Field(ge=1)
    run_type: Literal["outline", "lesson"]
    topic_ids: list[int] = Field(default_factory=list, max_length=100)
    selection_mode: Literal["single", "selected", "all_approved"] = "single"
    request_key: str = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")

class RunComplete(BaseModel):
    response_text: str = Field(min_length=1, max_length=100000)
    model_config = ConfigDict(extra="forbid")

class OutlineImport(BaseModel):
    raw_response: str = Field(min_length=1, max_length=100000)
    provider: str = Field(default="manual", min_length=1, max_length=80)
    model_config = ConfigDict(extra="forbid")

class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    parent_topic_id: int | None = Field(default=None, ge=1)
    model_config = ConfigDict(extra="forbid")

class TopicEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    model_config = ConfigDict(extra="forbid")

class TopicOrder(BaseModel):
    topic_ids: list[int] = Field(min_length=1, max_length=500)
    model_config = ConfigDict(extra="forbid")

class TopicDecision(BaseModel):
    topic_ids: list[int] = Field(min_length=1, max_length=500)
    decision: str
    model_config = ConfigDict(extra="forbid")

class LessonCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    model_config = ConfigDict(extra="forbid")

class ContentCreate(BaseModel):
    markdown: str = Field(min_length=1, max_length=200000)
    raw_response: str | None = Field(default=None, max_length=200000)
    source_kind: str = Field(default="manual", max_length=40)
    provider: str = Field(default="manual", max_length=80)
    model_name: str | None = Field(default=None, max_length=120)
    model_config = ConfigDict(extra="forbid")

class ReviewDecision(BaseModel):
    decision: str
    model_config = ConfigDict(extra="forbid")

def _ready() -> None:
    if not config.is_enabled():
        raise HTTPException(status_code=404, detail="AI-learning module is disabled.")
    try:
        initialize_store()
    except (StoreSchemaError, sqlite3.DatabaseError) as exc:
        raise HTTPException(status_code=503, detail="AI-learning storage requires reviewed recovery.") from exc

def _call(function, *args, **kwargs):
    _ready()
    try:
        return function(*args, **kwargs)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/status")
def status() -> dict[str, Any]:
    return {"enabled": config.is_enabled(), "retrieval_enabled": config.retrieval_is_enabled(),
            "provider_mode": "user_confirmed_chrome_bridge",
            "note": "The extension inserts prompts but never clicks Send automatically; retrieval is not connected to the existing chat response path."}

@router.post("/runs", status_code=201)
def create_run(request: RunCreate) -> dict[str, Any]:
    return _call(service.create_run, request.course_id, run_type=request.run_type,
                 topic_ids=request.topic_ids, selection_mode=request.selection_mode,
                 request_key=request.request_key)

@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    return _call(service.get_run, run_id)

@router.post("/runs/{run_id}/start")
def start_run(run_id: int) -> dict[str, Any]:
    return _call(service.start_run, run_id)

@router.post("/runs/{run_id}/complete")
def complete_run(run_id: int, request: RunComplete) -> dict[str, Any]:
    return _call(service.complete_run, run_id, request.response_text)

@router.post("/courses", status_code=201)
def create_course(request: CourseCreate) -> dict[str, Any]:
    return _call(service.create_course, **request.model_dump())

@router.get("/courses")
def list_courses(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    items = _call(service.list_courses, limit)
    return {"items": items, "count": len(items)}

@router.get("/courses/{course_id}")
def get_course(course_id: int) -> dict[str, Any]:
    return _call(service.get_course, course_id)

@router.post("/courses/{course_id}/outline/import", status_code=201)
def import_outline(course_id: int, request: OutlineImport) -> dict[str, Any]:
    return _call(service.import_outline, course_id, request.raw_response, provider=request.provider)

@router.post("/courses/{course_id}/topics", status_code=201)
def add_topic(course_id: int, request: TopicCreate) -> dict[str, Any]:
    return _call(service.add_topic, course_id, **request.model_dump())

@router.patch("/topics/{topic_id}")
def edit_topic(topic_id: int, request: TopicEdit) -> dict[str, Any]:
    return _call(service.edit_topic, topic_id, **request.model_dump(exclude_unset=True))

@router.put("/courses/{course_id}/topic-order")
def reorder_topics(course_id: int, request: TopicOrder) -> dict[str, Any]:
    return {"items": _call(service.reorder_topics, course_id, request.topic_ids)}

@router.post("/topics/decision")
def decide_topics(request: TopicDecision) -> dict[str, Any]:
    return {"updated": _call(service.decide_topics, request.topic_ids, request.decision)}

@router.post("/topics/{topic_id}/lessons", status_code=201)
def create_lesson(topic_id: int, request: LessonCreate) -> dict[str, Any]:
    return _call(service.create_lesson, topic_id, request.title)

@router.get("/topics/{topic_id}/lessons")
def list_lessons(topic_id: int) -> dict[str, Any]:
    items = _call(service.list_lessons, topic_id)
    return {"items": items, "count": len(items)}

@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: int) -> dict[str, Any]:
    return _call(service.soft_delete_topic, topic_id)

@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int) -> dict[str, Any]:
    return _call(service.soft_delete_lesson, lesson_id)

@router.get("/lessons/{lesson_id}/versions")
def list_versions(lesson_id: int) -> dict[str, Any]:
    items = _call(service.list_versions, lesson_id)
    return {"items": items, "count": len(items)}

@router.post("/lessons/{lesson_id}/versions", status_code=201)
def add_content(lesson_id: int, request: ContentCreate) -> dict[str, Any]:
    return _call(service.add_content_version, lesson_id, **request.model_dump())

@router.post("/versions/{version_id}/review")
def review_content(version_id: int, request: ReviewDecision) -> dict[str, Any]:
    return _call(service.review_content, version_id, request.decision)

@router.post("/versions/{version_id}/publish")
def publish_content(version_id: int) -> dict[str, Any]:
    return _call(service.publish_content, version_id)

@router.get("/search")
def search(query: str = Query(min_length=1, max_length=2000), limit: int = Query(default=8, ge=1, le=20)) -> dict[str, Any]:
    if not config.retrieval_is_enabled():
        raise HTTPException(status_code=404, detail="AI-learning knowledge retrieval is disabled.")
    return _call(service.search_knowledge, query, limit)
