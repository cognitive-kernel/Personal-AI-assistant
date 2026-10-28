from __future__ import annotations
import ipaddress
import sqlite3
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any, Literal
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StrictBool, StrictInt, ConfigDict
from . import backup_scheduler, assessment_review, browser_learning, knowledge_lifecycle_tasks, learning_knowledge_graph_tasks, learning_capabilities_routes, learning_recommendations, spaced_repetition, chat_history, chat_research, cloud_resources, curricula, db, experience_tasks, factual_verification_tasks, learning, ollama, programming_tasks, research_planner, research_tasks, sandbox_tasks, storage_health, storage_routes, task_manager
from .learning_tasks import LEARNING_TASK_TYPE, enqueue_learning_task, handle_learning_job, list_learning_escalations, reconcile_queued_learning_tasks, resolve_learning_escalation
from . import learning_review_tasks
from .learning_observability import collect_learning_operational_metrics
from .task_runtime import TaskRuntime
from .config import defaults
from .hardware import detect_hardware, runtime_limits
from .task_routes import require_loopback_client, router as task_router
from .cloud_resource_routes import router as cloud_resource_router
from .sandbox_routes import router as sandbox_router
from .experience_routes import router as experience_router
from .research_routes import router as research_router
from .programming_routes import router as programming_router
from .self_improvement_routes import router as self_improvement_router
from .factual_verification_routes import router as factual_verification_router
from .learning_candidate_routes import router as learning_candidate_router
from .learning_review_routes import router as learning_review_router
from .learning_source_sync_routes import router as learning_source_sync_router
from .knowledge_base_routes import router as knowledge_base_router
from .curriculum_evolution_routes import router as curriculum_evolution_router
from .learning_prerequisites_routes import router as learning_prerequisites_router
from .learning_knowledge_graph_routes import router as learning_knowledge_graph_router
from .knowledge_lifecycle_routes import router as knowledge_lifecycle_router
from .ai_learning.routes import router as ai_learning_router
from .ai_learning import config as ai_learning_config, integration as ai_learning_integration
from .ai_learning import browser_automation as ai_learning_browser_automation
from .ai_learning.ui import router as ai_learning_ui_router
from .schema_migrations import UntrackedSchemaError

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run blocking startup/shutdown work outside the event loop."""
    try:
        await run_in_threadpool(startup)
        yield
    finally:
        await run_in_threadpool(shutdown)


app = FastAPI(
    title="Personal AI Assistant",
    version="0.1.0",
    description="Local-first personal assistant; learning storage is isolated from settings.",
    lifespan=lifespan,
)

app.include_router(task_router)
app.include_router(cloud_resource_router)
app.include_router(sandbox_router)
app.include_router(experience_router)
app.include_router(research_router)
app.include_router(programming_router)
app.include_router(self_improvement_router)
app.include_router(factual_verification_router)
app.include_router(learning_candidate_router)
app.include_router(learning_review_router)
app.include_router(learning_source_sync_router)
app.include_router(curriculum_evolution_router)
app.include_router(learning_prerequisites_router)
app.include_router(learning_knowledge_graph_router)
app.include_router(knowledge_lifecycle_router)
app.include_router(knowledge_base_router)
app.include_router(learning_capabilities_routes.router)
app.include_router(ai_learning_router)
app.include_router(ai_learning_ui_router)
app.include_router(storage_routes.router)

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


@app.middleware("http")
async def report_storage_schema_block(request, call_next):
    """Keep the local dashboard available when a legacy DB needs reviewed recovery."""
    if _startup_storage_error and request.url.path not in {
        "/", "/api/health", "/openapi.json", "/docs", "/redoc"
    } and not request.url.path.startswith("/static/"):
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "راه‌اندازی پایگاه داده متوقف شد، چون ساختار دیتابیس موجود با نسخه‌های "
                    "ثبت‌شده سازگار نیست. هیچ مهاجرت یا تعمیری انجام نشده است. "
                    "گزارش فقط‌خواندنی scripts/inspect_sqlite_schema.py را اجرا کنید؛ "
                    "دیتابیس را حذف یا بازنشانی نکنید."
                ),
                "code": "untracked_storage_schema",
                "recovery_command": (
                    "python scripts/inspect_sqlite_schema.py <database-path> "
                    "--kind learning --output <report-path>"
                ),
            },
        )
    return await call_next(request)


@app.middleware("http")
async def enforce_loopback_only(request, call_next):
    """Block unauthenticated dashboard and API access from non-loopback peers."""
    client = request.client
    if client is None:
        return JSONResponse(
            status_code=403,
            content={"detail": "This application is available from localhost only."},
        )
    try:
        peer = ipaddress.ip_address(client.host)
    except ValueError:
        # Starlette's in-process TestClient uses this synthetic peer name; real
        # TCP peers are IP literals. Do not accept arbitrary non-IP peer values.
        if client.host != "testclient":
            return JSONResponse(
                status_code=403,
                content={"detail": "This application is available from localhost only."},
            )
    else:
        if not peer.is_loopback:
            return JSONResponse(
                status_code=403,
                content={"detail": "This application is available from localhost only."},
            )
    return await call_next(request)


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Serve the local app icon so browsers do not log a missing favicon."""
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/vnd.microsoft.icon")

_task_runtimes: list[TaskRuntime] = []
_startup_storage_error: str | None = None


def startup() -> None:
    global _task_runtimes, _startup_storage_error
    _startup_storage_error = None
    try:
        db.init_databases()
        browser_learning.recover_interrupted_courses()
    except UntrackedSchemaError:
        # Fail closed for data mutations, but keep the local UI and health endpoint
        # available so the operator gets a useful recovery instruction instead of
        # a browser-level "Failed to fetch". No migration or repair is attempted.
        _startup_storage_error = "untracked_storage_schema"
        logger.exception(
            "Startup blocked by an unrecognized SQLite schema; databases were not repaired."
        )
        return
    try:
        cloud_resources.initialize_store()
        cloud_resources.ensure_daily_discovery_scheduled()
    except Exception:
        # Cloud discovery is an optional subsystem; storage/network failures must not
        # prevent the local chat and learning runtime from starting.
        logger.exception("Cloud resource discovery could not be initialized or scheduled.")
    # Repair the crash window between creating a learning record and enqueueing it.
    reconcile_queued_learning_tasks()
    # Recover only explicit, persisted AI-learning runs. This never auto-generates
    # new topics/lessons and respects the durable global pause flag.
    if ai_learning_config.is_enabled():
        try:
            ai_learning_browser_automation.recover_pending_runs()
        except Exception:
            logger.exception("AI-learning persisted queue recovery failed; the dashboard remains available.")
    if any(runtime.is_running for runtime in _task_runtimes):
        backup_scheduler.start_backup_scheduler()
        return
    settings = db.get_settings()
    chat_handlers = {
        research_tasks.RESEARCH_TASK_TYPE: research_tasks.handle_research_task,
        research_planner.RESEARCH_PLAN_TASK_TYPE: research_planner.handle_research_plan_task,
    }
    learning_handlers = {
        LEARNING_TASK_TYPE: handle_learning_job,
        learning_review_tasks.LEARNING_REVIEW_TASK_TYPE: learning_review_tasks.handle_candidate_review,
        learning_review_tasks.LEARNING_SANDBOX_REVIEW_TASK_TYPE: learning_review_tasks.handle_candidate_sandbox_review,
        knowledge_lifecycle_tasks.KNOWLEDGE_VALIDATE_TASK_TYPE: knowledge_lifecycle_tasks.handle_validate_candidate,
        learning_knowledge_graph_tasks.KNOWLEDGE_GRAPH_REVIEW_TASK_TYPE: learning_knowledge_graph_tasks.handle_review_graph_edge,
    }
    programming_handlers: dict[str, Any] = {
        programming_tasks.PROGRAMMING_TASK_TYPE: programming_tasks.handle_programming_task,
    }
    system_handlers = {
        cloud_resources.CLOUD_DISCOVERY_TASK_TYPE: cloud_resources.handle_discovery_task,
    }
    sandbox_handler = sandbox_tasks.handler_from_environment()
    if sandbox_handler is not None:
        programming_handlers[sandbox_tasks.SANDBOX_TASK_TYPE] = sandbox_handler
    sandbox_language_handler = sandbox_tasks.handler_from_environment(allow_language_runners=True)
    if sandbox_language_handler is not None:
        programming_handlers[sandbox_tasks.SANDBOX_LANGUAGE_TASK_TYPE] = sandbox_language_handler
    experience_test_handler = experience_tasks.handler_from_environment()
    if experience_test_handler is not None:
        programming_handlers[experience_tasks.EXPERIENCE_TEST_TASK_TYPE] = experience_test_handler
    factual_verification_handler = factual_verification_tasks.handler_from_environment(sandbox_language_handler)
    if factual_verification_handler is not None:
        programming_handlers[factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE] = factual_verification_handler

    # Each runtime claims only its own task types, so slow learning or code-test
    # work cannot consume the chat lane's worker slots.
    runtime_specs: list[tuple[dict[str, Any], int, str, Any]] = [
        (chat_handlers, int(settings.get("chat_max_concurrent_jobs", 1)), "chat-worker", None),
        (learning_handlers, int(settings.get("learning_max_concurrent_jobs", 1)), "learning-worker", learning.maintain_factual_knowledge),
        (system_handlers, 1, "system-worker", None),
    ]
    if programming_handlers:
        runtime_specs.append((
            programming_handlers,
            int(settings.get("programming_max_concurrent_jobs", 1)),
            "programming-worker",
            sandbox_tasks.cleanup_terminal_containers if sandbox_handler is not None else None,
        ))

    _task_runtimes = []
    for lane_handlers, configured_workers, prefix, maintenance in runtime_specs:
        runtime = TaskRuntime(
            lane_handlers,
            worker_count=max(1, min(8, configured_workers)),
            worker_prefix=prefix,
            poll_interval=0.5,
            lease_seconds=300,
            maintenance_callback=maintenance,
            maintenance_interval=30.0,
            restrict_task_types=True,
        )
        runtime.start()
        _task_runtimes.append(runtime)

    # Preserve fail-closed behavior for tasks whose type has no registered handler.
    known_task_types = tuple(
        task_type for lane_handlers, _count, _prefix, _maintenance in runtime_specs
        for task_type in lane_handlers
    )
    fallback_placeholder = "__unregistered_task_type__"
    fallback_runtime = TaskRuntime(
        {fallback_placeholder: lambda *_args: {"ok": True}},
        worker_count=1,
        worker_prefix="dead-letter-worker",
        poll_interval=0.5,
        lease_seconds=300,
        exclude_task_types=known_task_types + (fallback_placeholder,),
    )
    fallback_runtime.start()
    _task_runtimes.append(fallback_runtime)
    backup_scheduler.start_backup_scheduler()

def shutdown() -> None:
    backup_scheduler.stop_backup_scheduler(timeout=5.0)
    for runtime in _task_runtimes:
        if not runtime.stop(timeout=10.0):
            logger.warning("Task runtime %s did not stop before the shutdown deadline.", runtime.worker_prefix)

@app.get("/ai-learning", include_in_schema=False)
def ai_learning_page() -> FileResponse:
    """Serve the isolated learning console only when its feature flag is enabled."""
    from .ai_learning.config import is_enabled
    if not is_enabled():
        raise HTTPException(status_code=404, detail="AI-learning module is disabled.")
    return FileResponse(STATIC_DIR / "ai-learning.html")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


_DASHBOARD_PAGE_IDS = {
    "storage-health-section",
    "chat-section",
    "learning-section",
    "learning-paths-section",
    "knowledge-section",
    "task-manager-section",
    "experience-memory-section",
    "cloud-resources-section",
    "cloud-plan-section",
    "kaggle-quota-section",
    "oci-limits-section",
    "sandbox-section",
    "self-improvement-section",
    "settings-section",
}


@app.get("/page/{page_id}", include_in_schema=False)
def dashboard_section_page(page_id: str) -> FileResponse:
    """Serve the shared dashboard shell for one allowlisted, separately addressable section."""
    if page_id not in _DASHBOARD_PAGE_IDS:
        raise HTTPException(status_code=404, detail="Dashboard page not found.")
    return FileResponse(STATIC_DIR / "index.html")

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)

class SettingsPatch(BaseModel):
    values: dict[str, Any]

class LearningRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    source_text: str | None = Field(default=None, max_length=100000)

class BrowserLearningRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    cdp_url: str = Field(default="http://127.0.0.1:9222", min_length=1, max_length=200)
    max_lessons: int = Field(default=20, ge=1, le=40)

    model_config = ConfigDict(extra="forbid")

class LearningEscalationResolveRequest(BaseModel):
    status: Literal["reviewed", "dismissed"]
    review_note: str = Field(default="", max_length=2000)

    model_config = ConfigDict(extra="forbid")

class AssessmentSubmission(BaseModel):
    answers: list[str] = Field(min_length=1, max_length=3)

class SemanticAssessmentReviewRequest(BaseModel):
    confirm: StrictBool

    model_config = ConfigDict(extra="forbid")

class SpacedRepetitionRequest(BaseModel):
    rating: StrictInt = Field(ge=0, le=5)

    model_config = ConfigDict(extra="forbid")

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

@app.get("/api/learning/operational-metrics", dependencies=[Depends(require_loopback_client)])
def learning_operational_metrics() -> dict[str, Any]:
    """Expose durable failure rates, budget pressure, and explicit release guardrails."""
    if _startup_storage_error:
        raise HTTPException(status_code=503, detail="Learning storage is blocked pending reviewed recovery.")
    try:
        with db.learning_connection() as connection:
            return collect_learning_operational_metrics(connection)
    except (OSError, sqlite3.Error, KeyError) as exc:
        logger.warning("Learning operational metrics unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Learning operational metrics are temporarily unavailable.") from exc


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    if _startup_storage_error:
        return {
            "status": "degraded",
            "reason": _startup_storage_error,
            "storage": {"status": "blocked", "mutations_enabled": False},
            "message": (
                "An existing SQLite schema needs a reviewed recovery path. "
                "No migration or repair was attempted."
            ),
            "recovery_command": (
                "python scripts/inspect_sqlite_schema.py <database-path> "
                "--kind learning --output <report-path>"
            ),
        }
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


@app.get("/api/conversations")
def list_chat_conversations(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"conversations": chat_history.list_conversations(limit=limit)}


@app.post("/api/conversations", status_code=201)
def create_chat_conversation() -> dict[str, Any]:
    return {"conversation": chat_history.create_conversation()}


@app.delete("/api/conversations/{conversation_id}")
def delete_chat_conversation(conversation_id: int) -> dict[str, Any]:
    if not chat_history.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Chat conversation not found.")
    return {"deleted": True, "conversation_id": conversation_id}


@app.get("/api/conversations/{conversation_id}/messages")
def list_chat_messages(conversation_id: int) -> dict[str, Any]:
    try:
        conversation = chat_history.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Chat conversation not found.")
        return {"conversation": conversation, "messages": chat_history.list_messages(conversation_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat conversation not found.") from exc


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    chat_limit = int(db.get_settings().get("chat_source_max_chars", 12000))
    if len(request.message) > chat_limit:
        raise HTTPException(status_code=422, detail=f"message exceeds the configured chat input limit of {chat_limit} characters.")
    try:
        if ai_learning_config.retrieval_is_enabled() and chat_research.should_use_research(request.message):
            try:
                knowledge_context = ai_learning_integration.retrieve_context(request.message)
            except Exception as retrieval_error:
                # Optional local knowledge must never make the established chat path unavailable.
                logger.warning("Optional AI-learning retrieval failed (%s)", type(retrieval_error).__name__)
                knowledge_context = []
            if knowledge_context:
                return {"reply": chat_research.answer(
                    request.message, request.history, knowledge_context=knowledge_context
                )}
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
    if "gpu_enabled" in values and not isinstance(values["gpu_enabled"], bool):
        raise HTTPException(status_code=422, detail="gpu_enabled must be a boolean.")
    keep_alive_values = {"auto", "0", "1m", "5m", "10m", "30m"}
    for purpose in ("chat", "learning", "programming"):
        key = f"{purpose}_model_keep_alive"
        if key in values and (not isinstance(values[key], str) or values[key] not in keep_alive_values):
            raise HTTPException(status_code=422, detail=f"{key} must be one of: auto, 0, 1m, 5m, 10m, 30m.")
    numeric_ranges = [
        ("ollama_timeout_seconds", 5, 600), ("ollama_num_ctx", 512, 8192),
        ("ollama_num_thread", 0, 64),
        ("cpu_resource_limit_percent", 10, 95),
        ("ram_resource_limit_percent", 10, 95),
        ("gpu_resource_limit_percent", 10, 95),
        ("ram_budget_max_gb", 1, 25),
        ("ram_safety_reserve_gb", 1, 8),
    ]
    for prefix in ("chat", "learning", "programming"):
        numeric_ranges.extend([
            (f"{prefix}_ollama_timeout_seconds", 5, 600),
            (f"{prefix}_ollama_num_ctx", 512, 8192),
            (f"{prefix}_ollama_num_thread", 0, 64),
            (f"{prefix}_max_concurrent_jobs", 1, 8),
        ])
    numeric_ranges.extend([
        ("chat_source_max_chars", 1000, 12000),
        ("learning_source_max_chars", 1000, 100000),
        ("programming_source_max_chars", 1000, 65536),
    ])
    for key, low, high in numeric_ranges:
        if key in values:
            try:
                raw_value = values[key]
                if isinstance(raw_value, bool):
                    raise ValueError
                values[key] = int(raw_value)
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f"{key} must be an integer.") from exc
            if not low <= values[key] <= high:
                raise HTTPException(status_code=422, detail=f"{key} must be between {low} and {high}.")
    for key in ("ollama_model", "chat_ollama_model", "learning_ollama_model", "programming_ollama_model"):
        if key in values and (
            not isinstance(values[key], str) or not values[key].strip() or len(values[key]) > 200
        ):
            raise HTTPException(status_code=422, detail=f"{key} must be a non-empty string of at most 200 characters.")
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


@app.post("/api/learning/browser/courses", status_code=202)
def create_browser_learning_course(request: BrowserLearningRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        course = browser_learning.create_course(request.topic, request.cdp_url, request.max_lessons)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except browser_learning.BrowserLearningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(browser_learning.run_course, int(course["id"]))
    return course


@app.get("/api/learning/browser/courses")
def list_browser_learning_courses(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"courses": browser_learning.list_courses(limit)}


@app.get("/api/learning/browser/courses/{course_id}")
def get_browser_learning_course(course_id: int) -> dict[str, Any]:
    try:
        return browser_learning.get_course(course_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Browser learning course not found.") from exc


@app.post("/api/learning/browser/courses/{course_id}/retry", status_code=202)
def retry_browser_learning_course(course_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        course = browser_learning.retry_course(course_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Browser learning course not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(browser_learning.run_course, course_id)
    return course


@app.get("/api/learning/escalations")
def learning_escalations(
    status: Literal["pending", "reviewed", "dismissed"] = "pending",
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        items = list_learning_escalations(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"escalations": items, "count": len(items), "status": status}


@app.patch("/api/learning/escalations/{escalation_id}")
def update_learning_escalation(
    escalation_id: int,
    request: LearningEscalationResolveRequest,
) -> dict[str, Any]:
    try:
        item = resolve_learning_escalation(
            escalation_id, request.status, request.review_note
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning escalation not found.") from exc
    except ValueError as exc:
        status_code = 409 if "already been resolved" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"escalation": item}

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
