from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import curricula, db, learning, ollama, experience, agent_loop
from .config import defaults
from .hardware import detect_hardware, runtime_limits

app = FastAPI(title="Personal AI Assistant", version="0.1.0", description="Local-first personal assistant; learning storage is isolated from settings.")

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

@app.on_event("startup")
def startup() -> None:
    db.init_databases()
    experience.init_experience_tables()

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)

class SettingsPatch(BaseModel):
    values: dict[str, Any]

class KnowledgeSourceRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=300)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class LearningRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    source_text: str | None = Field(default=None, max_length=100000)

class ExperienceCreateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    problem: str = Field(min_length=1, max_length=10000)
    hypothesis: str = Field(default="", max_length=5000)


class ExperienceUpdateRequest(BaseModel):
    status: str | None = None
    source_evidence: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    test_plan: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    test_results: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    takeaway: str | None = Field(default=None, max_length=10000)


class AssessmentSubmission(BaseModel):
    answers: list[str] = Field(min_length=1, max_length=3)

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
    return {"status": "ok", "ollama": ollama.health(), "hardware": hardware,
            "runtime_limits": runtime_limits(settings, hardware),
            "storage": {"settings_database": defaults.settings_db_path, "learning_database": defaults.learning_db_path,
                        "separate": defaults.settings_db_path != defaults.learning_db_path}}

@app.get("/api/models")
def models() -> dict[str, Any]:
    try:
        return {"models": ollama.list_models()}
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    try:
        return {"reply": agent_loop.answer_with_learning_loop(request.message, request.history)}
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


@app.post("/api/knowledge/sources", status_code=201)
def ingest_knowledge_source(request: KnowledgeSourceRequest) -> dict[str, Any]:
    from . import knowledge_base
    try:
        return knowledge_base.ingest_url(request.url, title=request.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Source could not be fetched: {exc}") from exc


@app.post("/api/knowledge/search")
def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
    from . import knowledge_base
    return {"query": request.query, "results": knowledge_base.search(request.query, request.limit)}


@app.get("/api/knowledge/sources")
def list_knowledge_sources() -> dict[str, Any]:
    from . import knowledge_base
    return {"sources": knowledge_base.list_sources()}


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


@app.post("/api/learning/experiences", status_code=201)
def create_learning_experience(request: ExperienceCreateRequest) -> dict[str, Any]:
    try:
        return experience.create_experience(request.topic, request.problem, request.hypothesis)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/learning/experiences")
def list_learning_experiences(topic: str | None = None, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    try:
        return {"experiences": experience.list_experiences(topic=topic, status=status, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/learning/experiences/{experience_id}")
def get_learning_experience(experience_id: int) -> dict[str, Any]:
    try:
        return experience.get_experience(experience_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning experience not found.") from exc


@app.patch("/api/learning/experiences/{experience_id}")
def update_learning_experience(experience_id: int, request: ExperienceUpdateRequest) -> dict[str, Any]:
    try:
        return experience.update_experience(
            experience_id,
            status=request.status,
            source_evidence=request.source_evidence,
            test_plan=request.test_plan,
            test_results=request.test_results,
            takeaway=request.takeaway,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning experience not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
