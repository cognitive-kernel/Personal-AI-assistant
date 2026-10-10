from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import curricula, db, learning, ollama
from .config import defaults
from .hardware import detect_hardware, runtime_limits

app = FastAPI(title="Personal AI Assistant", version="0.1.0", description="Local-first personal assistant; learning storage is isolated from settings.")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.on_event("startup")
def startup() -> None:
    db.init_databases()

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
        return {"reply": ollama.chat(request.message, request.history)}
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


@app.get("/api/knowledge")
def knowledge_items(limit: int = 100) -> dict[str, Any]:
    with db.learning_connection() as conn:
        rows = conn.execute(
            """SELECT id, topic, content, source_kind, version, status, job_id, created_at
               FROM knowledge_items ORDER BY id DESC LIMIT ?""",
            (max(1, min(300, limit)),),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}

@app.get("/api/learning/jobs/{job_id}")
def learning_job(job_id: int) -> dict[str, Any]:
    try:
        return learning.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Learning job not found.") from exc
