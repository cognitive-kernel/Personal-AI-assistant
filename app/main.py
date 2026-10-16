from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt
from . import assessment_review, learning_recommendations, spaced_repetition, chat_research, cloud_resources, curricula, db, experience_tasks, learning, ollama, research_tasks, sandbox_tasks, storage_health, task_manager
from .learning_tasks import LEARNING_TASK_TYPE, enqueue_learning_task, handle_learning_job, reconcile_queued_learning_tasks
from .task_runtime import TaskRuntime
from .config import defaults
from .hardware import detect_hardware, runtime_limits
from .task_routes import require_loopback_client, router as task_router
from .cloud_resource_routes import router as cloud_resource_router
from .sandbox_routes import router as sandbox_router
from .experience_routes import router as experience_router
from .research_routes import router as research_router
from .self_improvement_routes import router as self_improvement_router

app = FastAPI(title="Personal AI Assistant", version="0.1.0", description="Local-first personal assistant; learning storage is isolated from settings.")

app.include_router(task_router)
app.include_router(cloud_resource_router)
app.include_router(sandbox_router)
app.include_router(experience_router)
app.include_router(research_router)
app.include_router(self_improvement_router)

logger = logging.getLogger("personal_ai_assistant.http")

# توضیح فارسی برای 404ها؛ این لاگ‌ها علت احتمالی را مشخص می‌کنند و جایگزین رفع خطای واقعی نیستند.
_KNOWN_404_EXPLANATIONS = {
    "/learning/status": "مسیر وضعیت یادگیری در نسخه فعلی تعریف نشده است؛ نسخه جدید برنامه را دریافت و سرور را دوباره اجرا کنید.",
    "/learning/active": "مسیر کارهای فعال یادگیری در نسخه فعلی تعریف نشده است؛ نسخه جدید برنامه را دریافت و سرور را دوباره اجرا کنید.",
    "/scheduler/status": "مسیر وضعیت زمان‌بند تعریف نشده است؛ ممکن است زمان‌بندی خودکار هنوز پیاده‌سازی نشده باشد.",
    "/.well-known/appspecific/com.chrome.devtools.json": "درخواست خودکار Chrome DevTools است؛ معمولاً برای عملکرد برنامه ضروری نیست و می‌توان آن را نادیده گرفت.",
}

@app.middleware("http")
async def log_http_error_explanation(request, call_next):
    response = await call_next(request)
    if response.status_code == 404:
        path = request.url.path
        explanation = _KNOWN_404_EXPLANATIONS.get(
            path,
            "مسیر در سرور پیدا نشد؛ آدرس درخواست، نسخه اجراشده برنامه و endpointهای تعریف‌شده را بررسی کنید.",
        )
        # درخواست شناخته‌شده DevTools خطای کاربردی محسوب نمی‌شود؛ سایر 404ها هشدار هستند.
        if path == "/.well-known/appspecific/com.chrome.devtools.json":
            logger.info("توضیح فارسی 404 | مسیر: %s | دلیل: %s", path, explanation)
        else:
            logger.warning("توضیح فارسی 404 | مسیر: %s | دلیل: %s", path, explanation)
    return response
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_task_runtime: TaskRuntime | None = None


@app.on_event("startup")
def startup() -> None:
    global _task_runtime
    db.init_databases()
    try:
        cloud_resources.initialize_store()
        cloud_resources.ensure_daily_discovery_scheduled()
    except Exception:
        # Cloud discovery is an optional subsystem; storage/network failures must not
        # prevent the local chat and learning runtime from starting.
        logger.exception("Cloud resource discovery could not be initialized or scheduled.")
    # Repair the crash window between creating a learning record and enqueueing it.
    reconcile_queued_learning_tasks()
    if _task_runtime is not None and _task_runtime.is_running:
        return
    settings = db.get_settings()
    configured_workers = int(settings.get("learning_max_concurrent_jobs", 1))
    worker_count = max(1, min(8, configured_workers))
    handlers = {
        LEARNING_TASK_TYPE: handle_learning_job,
        research_tasks.RESEARCH_TASK_TYPE: research_tasks.handle_research_task,
        cloud_resources.CLOUD_DISCOVERY_TASK_TYPE: cloud_resources.handle_discovery_task,
    }
    sandbox_handler = sandbox_tasks.handler_from_environment()
    if sandbox_handler is not None:
        handlers[sandbox_tasks.SANDBOX_TASK_TYPE] = sandbox_handler
    experience_test_handler = experience_tasks.handler_from_environment()
    if experience_test_handler is not None:
        handlers[experience_tasks.EXPERIENCE_TEST_TASK_TYPE] = experience_test_handler
    _task_runtime = TaskRuntime(
        handlers,
        worker_count=worker_count,
        poll_interval=0.5,
        lease_seconds=300,
        maintenance_callback=(
            sandbox_tasks.cleanup_terminal_containers if sandbox_handler is not None else None
        ),
        maintenance_interval=30.0,
    )
    _task_runtime.start()


@app.on_event("shutdown")
def shutdown() -> None:
    if _task_runtime is not None and not _task_runtime.stop(timeout=10.0):
        logger.warning("Task runtime did not stop before the shutdown deadline.")

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)

class SettingsPatch(BaseModel):
    values: dict[str, Any]

class LearningRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    source_text: str | None = Field(default=None, max_length=100000)

class AssessmentSubmission(BaseModel):
    answers: list[str] = Field(min_length=1, max_length=3)

class SemanticAssessmentReviewRequest(BaseModel):
    confirm: StrictBool

    class Config:
        extra = "forbid"

class SpacedRepetitionRequest(BaseModel):
    rating: StrictInt = Field(ge=0, le=5)

    class Config:
        extra = "forbid"

class CurriculumEnrollment(BaseModel):
    slug: str = Field(min_length=1, max_length=100)

class LessonReviewSubmission(BaseModel):
    outcomes: list[str] = Field(min_length=1, max_length=5)
    value_rating: int = Field(ge=1, le=5)
    takeaway: str = Field(default="", max_length=3000)
    issues: str = Field(default="", max_length=3000)

class PathTopicProgress(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    latest_score: float | None = Field(default=None, ge=0, le=100)

@app.get("/api/health")
def api_health() -> dict[str, Any]:
    hardware = detect_hardware()
    settings = db.get_settings()
    configured_paths = (
        defaults.settings_db_path,
        defaults.learning_db_path,
        defaults.cloud_resources_db_path,
    )
    return {
        "status": "ok",
        "ollama": ollama.health(),
        "hardware": hardware,
        "runtime_limits": runtime_limits(settings, hardware),
        "storage": {
            "settings_database_configured": bool(configured_paths[0]),
            "learning_database_configured": bool(configured_paths[1]),
            "cloud_resources_database_configured": bool(configured_paths[2]),
            "separate": len(set(configured_paths)) == 3,
        },
    }


@app.get("/api/health/storage", dependencies=[Depends(require_loopback_client)])
def api_storage_health() -> dict[str, Any]:
    """Read-only local diagnostics; never migrate, repair, or expose database paths."""
    return storage_health.check_storage_health()

@app.get("/api/models")
def models() -> dict[str, Any]:
    try:
        return {"models": ollama.list_models()}
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    try:
        return {"reply": chat_research.answer(request.message, request.history)}
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return db.get_settings()

@app.patch("/api/settings")
def patch_settings(request: SettingsPatch) -> dict[str, Any]:
    values = dict(request.values)
    if "ollama_base_url" in values:
        url = str(values["ollama_base_url"]).rstrip("/")
        if not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:") or url.startswith("http://[::1]:") or url in {"http://127.0.0.1", "http://localhost", "http://[::1]"}):
            raise HTTPException(status_code=422, detail="Only loopback Ollama URLs are supported.")
        values["ollama_base_url"] = url
    for key, low, high in [("ollama_timeout_seconds", 5, 600), ("ollama_num_ctx", 512, 8192),
                           ("ollama_num_thread", 0, 64), ("learning_max_concurrent_jobs", 1, 2), ("learning_source_max_chars", 1000, 100000)]:
        if key in values:
            try:
                values[key] = int(values[key])
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f"{key} must be an integer.") from exc
            if not low <= values[key] <= high:
                raise HTTPException(status_code=422, detail=f"{key} must be between {low} and {high}.")
    if "ollama_model" in values and (not isinstance(values["ollama_model"], str) or not values["ollama_model"].strip() or len(values["ollama_model"]) > 200):
        raise HTTPException(status_code=422, detail="ollama_model must be a non-empty string of at most 200 characters.")
    try:
        return db.update_settings(values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.get("/api/learning/curricula")
def learning_curricula() -> dict[str, Any]:
    return {"curricula": curricula.list_curricula()}


@app.get("/api/learning/curricula/{slug}")
def learning_curriculum(slug: str) -> dict[str, Any]:
    result = curricula.get_curriculum(slug)
    if result is None:
        raise HTTPException(status_code=404, detail="Learning curriculum not found.")
    return result


@app.post("/api/learning/jobs", status_code=202)
def create_learning_job(request: LearningRequest) -> dict[str, Any]:
    try:
        return learning.submit_job(request.topic, request.source_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.get("/api/learning/jobs")
def learning_jobs(limit: int = 50) -> dict[str, Any]:
    return {"jobs": learning.list_jobs(limit)}

# Backward-compatible status endpoints used by older local UI clients.
@app.get("/learning/status", include_in_schema=False)
def legacy_learning_status() -> dict[str, Any]:
    with db.learning_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM learning_jobs GROUP BY status"
        ).fetchall()
        knowledge_count = conn.execute("SELECT COUNT(*) AS count FROM knowledge_items").fetchone()["count"]
        unverified_count = conn.execute(
            "SELECT COUNT(*) AS count FROM knowledge_items WHERE status='unverified'"
        ).fetchone()["count"]
    counts = {row["status"]: row["count"] for row in rows}
    return {
        "status": "ok",
        "jobs": {
            "total": sum(counts.values()),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "rejected": counts.get("rejected", 0),
            "failed": counts.get("failed", 0),
        },
        "knowledge": {"total": knowledge_count, "unverified": unverified_count},
        "web_learning": "not_implemented",
        "independent_verification": "not_implemented",
    }


@app.get("/learning/active", include_in_schema=False)
def legacy_active_learning() -> dict[str, Any]:
    with db.learning_connection() as conn:
        ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM learning_jobs WHERE status IN ('queued', 'running') ORDER BY id"
            ).fetchall()
        ]
    jobs = [learning.get_job(job_id) for job_id in ids]
    return {"count": len(jobs), "jobs": jobs}


@app.get("/scheduler/status", include_in_schema=False)
def legacy_scheduler_status() -> dict[str, Any]:
    # Report the actual implementation state rather than pretending a scheduler exists.
    return {
        "enabled": False,
        "status": "not_implemented",
        "message": "زمان‌بندی خودکار یادگیری هنوز پیاده‌سازی نشده است.",
    }


@app.get("/api/knowledge")
def knowledge_items(limit: int = 100) -> dict[str, Any]:
    with db.learning_connection() as conn:
        rows = conn.execute(
            """SELECT id, topic, content, source_kind, version, status, job_id, created_at
               FROM knowledge_items ORDER BY id DESC LIMIT ?""",
            (max(1, min(300, limit)),),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}

@app.get("/api/learning/paths")
def learning_paths() -> dict[str, Any]:
    return {"paths": learning.list_learning_paths()}


@app.post("/api/learning/paths", status_code=201)
def enroll_learning_path(request: CurriculumEnrollment) -> dict[str, Any]:
    try:
        return learning.enroll_curriculum(request.slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning curriculum not found.") from exc


@app.post("/api/learning/paths/{path_id}/sync")
def sync_learning_path(path_id: int) -> dict[str, Any]:
    try:
        return learning.sync_curriculum_path(path_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or curriculum not found.") from exc


@app.post("/api/learning/paths/{path_id}/start")
def start_learning_path(path_id: int) -> dict[str, Any]:
    try:
        return learning.start_path_learning(path_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc


@app.post("/api/learning/paths/{path_id}/stop")
def stop_learning_path(path_id: int) -> dict[str, Any]:
    try:
        return learning.stop_path_learning(path_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc


@app.post("/api/learning/paths/{path_id}/topics/{topic_id}/start")
def start_learning_path_topic(path_id: int, topic_id: int) -> dict[str, Any]:
    try:
        return learning.start_topic_learning(path_id, topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/learning/paths/{path_id}/topics/{topic_id}/regenerate", status_code=202)
def regenerate_learning_path_topic(path_id: int, topic_id: int) -> dict[str, Any]:
    try:
        return learning.regenerate_topic_learning(path_id, topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/learning/paths/{path_id}/topics/{topic_id}/stop")
def stop_learning_path_topic(path_id: int, topic_id: int) -> dict[str, Any]:
    try:
        return learning.stop_topic_learning(path_id, topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc


@app.patch("/api/learning/paths/{path_id}/topics/{topic_id}")
def update_learning_path_topic(path_id: int, topic_id: int, request: PathTopicProgress) -> dict[str, Any]:
    try:
        return learning.update_path_topic(path_id, topic_id, request.status, request.latest_score)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path or topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/learning/jobs/{job_id}/assessment")
def submit_learning_assessment(job_id: int, request: AssessmentSubmission) -> dict[str, Any]:
    try:
        return learning.submit_assessment(job_id, request.answers)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/learning/jobs/{job_id}/assessment/{attempt_id}/semantic-review",
    dependencies=[Depends(require_loopback_client)],
)
def review_learning_assessment(
    job_id: int,
    attempt_id: int,
    request: SemanticAssessmentReviewRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation for model-assisted review is required.")
    try:
        return assessment_review.review_assessment_attempt(job_id, attempt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Assessment attempt not found for this learning job.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ollama.OllamaError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Model-assisted assessment review is unavailable or invalid.") from exc


@app.get(
    "/api/learning/jobs/{job_id}/assessment/{attempt_id}/semantic-review",
    dependencies=[Depends(require_loopback_client)],
)
def get_learning_assessment_review(job_id: int, attempt_id: int) -> dict[str, Any]:
    try:
        result = assessment_review.get_assessment_review(job_id, attempt_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="No semantic review is stored for this attempt.")
    return {"job_id": job_id, "attempt_id": attempt_id, "semantic_review": result}


@app.get(
    "/api/learning/recommendations",
    dependencies=[Depends(require_loopback_client)],
)
def get_learning_recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    path_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    try:
        return learning_recommendations.get_learning_recommendations(
            limit=limit, path_id=path_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(
    "/api/learning/reviews/due",
    dependencies=[Depends(require_loopback_client)],
)
def list_due_learning_reviews(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    items = spaced_repetition.list_due_reviews(limit)
    return {"items": items, "count": len(items)}


@app.post(
    "/api/learning/paths/{path_id}/topics/{topic_id}/reviews",
    dependencies=[Depends(require_loopback_client)],
)
def record_learning_topic_review(
    path_id: int,
    topic_id: int,
    request: SpacedRepetitionRequest,
) -> dict[str, Any]:
    try:
        return spaced_repetition.record_review(path_id, topic_id, request.rating)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(
    "/api/learning/paths/{path_id}/topics/{topic_id}/reviews",
    dependencies=[Depends(require_loopback_client)],
)
def get_learning_topic_review_history(
    path_id: int,
    topic_id: int,
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return spaced_repetition.get_topic_review_history(path_id, topic_id, limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning path topic not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/learning/jobs/{job_id}/review")
def submit_learning_review(job_id: int, request: LessonReviewSubmission) -> dict[str, Any]:
    try:
        return learning.submit_lesson_review(
            job_id, request.outcomes, request.value_rating, request.takeaway, request.issues
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/learning/jobs/{job_id}")
def learning_job(job_id: int) -> dict[str, Any]:
    try:
        return learning.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning job not found.") from exc
