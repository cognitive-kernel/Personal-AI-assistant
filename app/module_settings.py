"""Central registry and runtime guards for user-configurable application modules."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

from .config import SETTING_DEFAULTS


MODULE_SETTING_KEYS: dict[str, str] = {
    "chat": "chat_enabled",
    "research_plan": "research_plan_enabled",
    "programming": "programming_enabled",
    "factual_review": "factual_review_enabled",
    "embedding": "embedding_enabled",
    "knowledge_base": "knowledge_base_enabled",
    "knowledge_graph": "knowledge_graph_enabled",
    "cloud_resources": "cloud_resources_enabled",
    "cloud_plan": "cloud_plan_enabled",
    "kaggle_quota": "kaggle_quota_enabled",
    "oci_quota": "oci_quota_enabled",
    "autonomous_engineering": "autonomous_engineering_enabled",
    "experience_system": "experience_system_enabled",
    "ai_learning": "ai_learning_enabled",
    "ai_learning_knowledge": "ai_learning_knowledge_enabled",
    "basic_learning": "basic_learning_enabled",
    "topic_learning": "topic_learning_enabled",
    "sandbox": "sandbox_enabled",
    "self_improvement": "self_improvement_enabled",
}


PURPOSE_MODULES: dict[str, str] = {
    "chat": "chat",
    "research_plan": "research_plan",
    "programming": "programming",
    "factual_review": "factual_review",
    "ai_learning": "ai_learning",
    "learning": "basic_learning",
    "topic_learning": "topic_learning",
    "embedding": "embedding",
}


class ModuleDisabledError(RuntimeError):
    """Raised when a capability is explicitly disabled in persisted settings."""

    def __init__(self, module: str) -> None:
        self.module = module
        super().__init__(f"Module '{module}' is disabled in settings.")


def _settings_snapshot() -> dict[str, Any]:
    from .db import get_settings

    try:
        return get_settings()
    except sqlite3.OperationalError as exc:
        if "no such table: settings" not in str(exc).casefold():
            raise
        return {}


def is_module_enabled(module: str, settings: dict[str, Any] | None = None) -> bool:
    key = MODULE_SETTING_KEYS.get(module)
    if key is None:
        raise KeyError(f"Unknown application module: {module}")
    current = _settings_snapshot() if settings is None else settings
    value = current.get(key, SETTING_DEFAULTS[key])
    if type(value) is not bool:
        raise RuntimeError(f"Persisted module setting {key} must be a boolean.")
    return value


def ensure_module_enabled(module: str, settings: dict[str, Any] | None = None) -> None:
    if not is_module_enabled(module, settings):
        raise ModuleDisabledError(module)


def require_module_enabled(module: str):
    """Return a FastAPI dependency for a module-level 409/503 guard."""
    def dependency() -> None:
        try:
            ensure_module_enabled(module)
        except ModuleDisabledError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Module settings for '{module}' are unavailable or invalid.",
            ) from exc

    return dependency
