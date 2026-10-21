"""Provider-neutral routing for model-backed tasks.

Ollama is the only built-in adapter. register_provider() is the code-level extension
point for a future custom/private LLM; configuration never imports arbitrary code.
"""
from __future__ import annotations
import re
from typing import Any, Protocol

_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")
TASK_CATALOG = [
    {"id": "simple_chat", "label": "گفتگوی ساده", "kind": "chat"},
    {"id": "research", "label": "پژوهش و پاسخ منبع‌محور", "kind": "chat"},
    {"id": "knowledge_learning", "label": "یادگیری و تولید دانش", "kind": "chat"},
    {"id": "programming", "label": "برنامه‌نویسی و تحلیل کد", "kind": "chat"},
    {"id": "claim_review", "label": "بازبینی ادعاها", "kind": "chat"},
    {"id": "assessment_review", "label": "بازبینی پاسخ یادگیرنده", "kind": "chat"},
    {"id": "embeddings", "label": "Embedding و بازیابی معنایی", "kind": "embedding"},
]
TASK_IDS = tuple(item["id"] for item in TASK_CATALOG)


class ModelRuntimeError(RuntimeError):
    """A configured provider or model could not execute the requested task."""


class ProviderAdapter(Protocol):
    def chat(self, message: str, *, model: str, history: list[dict[str, str]] | None,
             system_prompt: str | None, response_format: str | None,
             options: dict[str, Any] | None) -> str: ...
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...


_PROVIDERS: dict[str, ProviderAdapter] = {}


def register_provider(provider_id: str, adapter: ProviderAdapter) -> None:
    """Register a reviewed provider adapter in code; settings cannot register code."""
    if not isinstance(provider_id, str) or not _PROFILE_ID.fullmatch(provider_id):
        raise ValueError("provider_id must be a stable identifier.")
    if not callable(getattr(adapter, "chat", None)) or not callable(getattr(adapter, "embed", None)):
        raise ValueError("A provider adapter must implement chat() and embed().")
    _PROVIDERS[provider_id] = adapter


class _OllamaAdapter:
    def chat(self, message: str, *, model: str, history: list[dict[str, str]] | None,
             system_prompt: str | None, response_format: str | None,
             options: dict[str, Any] | None) -> str:
        from . import ollama
        return ollama.chat(message, history, system_prompt, model=model,
                           response_format=response_format, options=options)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        from . import ollama
        return ollama.embed(texts, model=model)


register_provider("ollama", _OllamaAdapter())


def default_model_configuration(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Translate legacy single-model settings into backwards-compatible profiles."""
    if settings is None:
        from .db import get_settings
        settings = get_settings()
    chat_model = str(settings.get("ollama_model") or "qwen2.5:7b").strip()
    embedding_model = str(settings.get("ollama_embedding_model") or "nomic-embed-text").strip()
    profiles = [
        {"id": "default-chat", "name": "مدل پیش‌فرض گفتگو", "provider": "ollama", "model": chat_model, "enabled": True},
        {"id": "default-embedding", "name": "مدل embedding", "provider": "ollama", "model": embedding_model, "enabled": True},
    ]
    assignments = {task_id: "default-chat" for task_id in TASK_IDS}
    assignments["embeddings"] = "default-embedding"
    return {"model_profiles": profiles, "task_model_assignments": assignments}


def validate_model_configuration(profiles: Any, assignments: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 32:
        raise ValueError("ثبت بین ۱ تا ۳۲ پروفایل مدل الزامی است.")
    clean_profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) - {"id", "name", "provider", "model", "enabled"}:
            raise ValueError("ساختار پروفایل مدل معتبر نیست.")
        profile_id, name = profile.get("id"), profile.get("name")
        provider, model, enabled = profile.get("provider"), profile.get("model"), profile.get("enabled")
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("شناسهٔ پروفایل باید با حرف/عدد شروع شود و فقط حروف، عدد، _ یا - داشته باشد.")
        if profile_id in seen:
            raise ValueError("شناسهٔ پروفایل‌ها باید یکتا باشد.")
        seen.add(profile_id)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("نام نمایشی پروفایل باید ۱ تا ۱۰۰ نویسه باشد.")
        if not isinstance(provider, str) or provider not in _PROVIDERS:
            raise ValueError("ارائه‌دهنده انتخاب نشده یا adapter آن در برنامه ثبت نشده است.")
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 200:
            raise ValueError("شناسه/نام مدل باید ۱ تا ۲۰۰ نویسه باشد.")
        if not isinstance(enabled, bool):
            raise ValueError("وضعیت فعال‌بودن پروفایل باید boolean باشد.")
        clean_profiles.append({"id": profile_id, "name": name.strip(), "provider": provider,
                               "model": model.strip(), "enabled": enabled})
    enabled_ids = {item["id"] for item in clean_profiles if item["enabled"]}
    if not enabled_ids:
        raise ValueError("حداقل یک پروفایل فعال لازم است.")
    if not isinstance(assignments, dict) or set(assignments) != set(TASK_IDS):
        raise ValueError("برای همهٔ انواع وظیفه باید یک تخصیص مدل تعریف شود.")
    clean_assignments: dict[str, str] = {}
    for task_id in TASK_IDS:
        profile_id = assignments.get(task_id)
        if not isinstance(profile_id, str) or profile_id not in enabled_ids:
            raise ValueError(f"تخصیص مدل برای وظیفهٔ {task_id} به پروفایل فعال نیاز دارد.")
        clean_assignments[task_id] = profile_id
    return clean_profiles, clean_assignments


def effective_model_configuration(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if settings is None:
        from .db import get_settings
        settings = get_settings()
    profiles, assignments = settings.get("model_profiles"), settings.get("task_model_assignments")
    if not isinstance(profiles, list) or not profiles:
        config = default_model_configuration(settings)
        profiles, assignments = config["model_profiles"], config["task_model_assignments"]
    try:
        profiles, assignments = validate_model_configuration(profiles, assignments)
    except ValueError as exc:
        raise ModelRuntimeError("پیکربندی مدل‌ها نامعتبر است: " + str(exc)) from exc
    return {
        "model_profiles": profiles,
        "task_model_assignments": assignments,
        "model_task_catalog": [dict(item) for item in TASK_CATALOG],
        "available_provider_adapters": sorted(_PROVIDERS),
    }


def resolve_model_profile(task_type: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if task_type not in TASK_IDS:
        raise ModelRuntimeError("نوع وظیفه برای انتخاب مدل ثبت نشده است.")
    config = effective_model_configuration(settings)
    profile_id = config["task_model_assignments"][task_type]
    profile = next(item for item in config["model_profiles"] if item["id"] == profile_id)
    if profile["provider"] not in _PROVIDERS:
        raise ModelRuntimeError("adapter ارائه‌دهنده برای این پروفایل در دسترس نیست.")
    return dict(profile)


def profile_cache_key(task_type: str, settings: dict[str, Any] | None = None) -> str:
    profile = resolve_model_profile(task_type, settings)
    return profile["provider"] + ":" + profile["model"]


def chat(message: str, history: list[dict[str, str]] | None = None, system_prompt: str | None = None,
         *, task_type: str = "simple_chat", response_format: str | None = None,
         options: dict[str, Any] | None = None) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ModelRuntimeError("متن ورودی مدل خالی است.")
    profile = resolve_model_profile(task_type)
    try:
        return _PROVIDERS[profile["provider"]].chat(
            message, model=profile["model"], history=history, system_prompt=system_prompt,
            response_format=response_format, options=options,
        )
    except Exception as exc:
        raise ModelRuntimeError(
            f"درخواست مدل برای وظیفهٔ {task_type} ناموفق بود ({profile['provider']}/{profile['model']}): {exc}"
        ) from exc


def embed(texts: list[str], *, task_type: str = "embeddings") -> list[list[float]]:
    profile = resolve_model_profile(task_type)
    try:
        return _PROVIDERS[profile["provider"]].embed(texts, model=profile["model"])
    except Exception as exc:
        raise ModelRuntimeError(
            f"تولید embedding برای وظیفهٔ {task_type} ناموفق بود ({profile['provider']}/{profile['model']}): {exc}"
        ) from exc
