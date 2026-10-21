"""Task-based LLM routing with provider adapters.

The application calls this module instead of binding business logic to Ollama's
model-name setting. Ollama is the built-in provider; a future private LLM can be
integrated by registering an adapter that implements the same completion contract.
Provider secrets must remain outside model profiles and the local settings database.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .db import get_settings
from .hardware import detect_hardware, runtime_limits

TASK_LABELS: dict[str, str] = {
    "simple_chat": "گفتگوی ساده",
    "knowledge_learning": "یادگیری و تولید درس",
    "programming": "برنامه‌نویسی و عیب‌یابی",
    "research": "پژوهش و پاسخ فنی",
    "research_review": "بازبینی ادعاها و شواهد",
    "assessment": "ارزیابی پاسخ‌های یادگیری",
}
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ProviderAdapter = Callable[
    [dict[str, Any], list[dict[str, str]], dict[str, Any], str | dict[str, Any] | None, float | None],
    dict[str, Any],
]


class LLMRoutingError(RuntimeError):
    """Raised when a task cannot be routed to a usable configured model."""


_PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {}


def register_provider_adapter(name: str, adapter: ProviderAdapter, *, replace: bool = False) -> None:
    """Register an inference backend. Adapters receive a profile and never receive stored secrets."""
    provider = name.strip().lower() if isinstance(name, str) else ""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", provider):
        raise ValueError("Provider name must be a lowercase identifier.")
    if not callable(adapter):
        raise ValueError("Provider adapter must be callable.")
    if provider in _PROVIDER_ADAPTERS and not replace:
        raise ValueError(f"Provider adapter is already registered: {provider}")
    _PROVIDER_ADAPTERS[provider] = adapter


def supported_providers() -> list[dict[str, str]]:
    return [
        {"id": provider, "label": "Ollama (محلی)" if provider == "ollama" else provider}
        for provider in sorted(_PROVIDER_ADAPTERS)
    ]


def normalize_model_profiles(value: Any, *, require_nonempty: bool = True) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("llm_models must be a list containing at most 32 model profiles.")
    if require_nonempty and not value:
        raise ValueError("At least one model profile is required.")
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Model profile {index + 1} must be an object.")
        profile_id = item.get("id")
        provider = item.get("provider", "ollama")
        model = item.get("model")
        enabled = item.get("enabled", True)
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError(f"Model profile {index + 1} has an invalid id.")
        if profile_id in seen:
            raise ValueError(f"Duplicate model profile id: {profile_id}")
        if not isinstance(provider, str) or provider not in _PROVIDER_ADAPTERS:
            raise ValueError(f"Unsupported LLM provider for profile {profile_id}.")
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 200:
            raise ValueError(f"Model name for profile {profile_id} must contain 1–200 characters.")
        if not isinstance(enabled, bool):
            raise ValueError(f"enabled for model profile {profile_id} must be a boolean.")
        label = item.get("label", profile_id)
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
            raise ValueError(f"Display label for model profile {profile_id} must contain 1–100 characters.")
        profiles.append({
            "id": profile_id,
            "label": label.strip(),
            "provider": provider,
            "model": model.strip(),
            "enabled": enabled,
        })
        seen.add(profile_id)
    if require_nonempty and not any(profile["enabled"] for profile in profiles):
        raise ValueError("At least one model profile must be enabled.")
    return profiles


def validate_routing_settings(values: dict[str, Any], current: dict[str, Any] | None = None) -> None:
    """Validate model profile edits together with task mappings before saving settings."""
    merged = dict(current or {})
    merged.update(values)
    raw_profiles = merged.get("llm_models", [])
    if isinstance(raw_profiles, list) and not raw_profiles:
        # Empty is retained only for legacy databases; the effective profile uses ollama_model.
        profiles = [{
            "id": "default-local",
            "provider": "ollama",
            "model": str(merged.get("ollama_model", "qwen2.5:7b")).strip(),
            "enabled": True,
        }]
    else:
        profiles = normalize_model_profiles(raw_profiles)
    by_id = {item["id"]: item for item in profiles}
    mapping = merged.get("llm_task_mapping", {})
    if not isinstance(mapping, dict):
        raise ValueError("llm_task_mapping must be an object mapping task names to model profile ids.")
    for task, profile_id in mapping.items():
        if task not in TASK_LABELS:
            raise ValueError(f"Unsupported task route: {task}")
        if not isinstance(profile_id, str) or profile_id not in by_id:
            raise ValueError(f"Task {task} refers to an unknown model profile.")
        if not by_id[profile_id]["enabled"]:
            raise ValueError(f"Task {task} cannot be assigned to a disabled model profile.")


def _effective_profiles(settings: dict[str, Any]) -> list[dict[str, Any]]:
    value = settings.get("llm_models")
    if isinstance(value, list) and value:
        try:
            return normalize_model_profiles(value)
        except ValueError as exc:
            raise LLMRoutingError(f"Model profiles are invalid: {exc}") from exc
    legacy_model = settings.get("ollama_model", "qwen2.5:7b")
    if not isinstance(legacy_model, str) or not legacy_model.strip():
        raise LLMRoutingError("No model profile is configured.")
    return [{"id": "default-local", "label": "مدل پیش‌فرض فعلی", "provider": "ollama", "model": legacy_model.strip(), "enabled": True}]


def resolve_model_profile(task: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a task to an enabled model profile, preserving legacy settings compatibility."""
    if task not in TASK_LABELS:
        raise LLMRoutingError(f"Unknown LLM task: {task}")
    current = settings if settings is not None else get_settings()
    profiles = _effective_profiles(current)
    enabled = [profile for profile in profiles if profile["enabled"]]
    if not enabled:
        raise LLMRoutingError("No enabled model profile is configured.")
    mapping = current.get("llm_task_mapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    selected_id = mapping.get(task) or enabled[0]["id"]
    selected = next((profile for profile in profiles if profile["id"] == selected_id), None)
    if selected is None:
        raise LLMRoutingError(f"Task {task} refers to a missing model profile.")
    if not selected["enabled"]:
        raise LLMRoutingError(f"The model profile assigned to task {task} is disabled.")
    if selected["provider"] not in _PROVIDER_ADAPTERS:
        raise LLMRoutingError(f"No adapter is registered for provider {selected['provider']}.")
    return dict(selected)


def selected_model_identity(task: str) -> str:
    profile = resolve_model_profile(task)
    return f"{profile['provider']}:{profile['model']}"[:240]


def complete(
    task: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    response_format: str | dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Dispatch a chat completion through the adapter assigned to the requested task."""
    if not isinstance(messages, list) or not messages or len(messages) > 64:
        raise LLMRoutingError("Messages must contain between 1 and 64 entries.")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise LLMRoutingError("Each message must have an allowed role and string content.")
    settings = get_settings()
    profile = resolve_model_profile(task, settings)
    adapter = _PROVIDER_ADAPTERS.get(profile["provider"])
    if adapter is None:
        raise LLMRoutingError(f"No adapter is registered for provider {profile['provider']}.")
    effective_options = dict(options or {})
    try:
        response = adapter(profile, messages, effective_options, response_format, timeout)
    except LLMRoutingError:
        raise
    if not isinstance(response, dict):
        raise LLMRoutingError("LLM provider returned an unexpected response shape.")
    return response


def complete_text(
    message: str,
    history: list[dict[str, str]] | None = None,
    *,
    system_prompt: str | None = None,
    task: str = "simple_chat",
) -> str:
    settings = get_settings()
    limits = runtime_limits(settings, detect_hardware())
    messages: list[dict[str, str]] = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()[:6000]})
    for item in history or []:
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
            messages.append({"role": item["role"], "content": item["content"][:12000]})
    if not isinstance(message, str) or not message.strip():
        raise LLMRoutingError("Chat message must be non-empty.")
    messages.append({"role": "user", "content": message[:12000]})
    response = complete(
        task,
        messages,
        options={"num_ctx": limits["num_ctx"], "num_thread": limits["cpu_threads"]},
    )
    result = response.get("message", {}).get("content")
    if not isinstance(result, str) or not result.strip():
        raise LLMRoutingError("LLM provider returned an empty or unexpected chat response.")
    return result


def _ollama_adapter(
    profile: dict[str, Any],
    messages: list[dict[str, str]],
    options: dict[str, Any],
    response_format: str | dict[str, Any] | None,
    timeout: float | None,
) -> dict[str, Any]:
    from .ollama import chat_completion

    return chat_completion(
        str(profile["model"]),
        messages,
        options=options,
        response_format=response_format,
        timeout=timeout,
    )


register_provider_adapter("ollama", _ollama_adapter)
