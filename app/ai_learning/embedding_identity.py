"""Stable provider-scoped identities for AI-learning embedding caches."""
from __future__ import annotations

from typing import Any


def embedding_cache_identity(settings: dict[str, Any], model: str) -> str:
    """Namespace vectors by provider, endpoint and model to prevent cross-provider reuse."""
    if not isinstance(settings, dict):
        raise TypeError("Embedding settings must be a mapping.")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Embedding model must be a non-empty name of at most 200 characters.")
    provider = settings.get("embedding_llm_provider", "ollama")
    if provider == "ollama":
        endpoint = settings.get("ollama_base_url", "http://127.0.0.1:11434")
    elif provider == "openai_compatible":
        endpoint = settings.get("embedding_llm_base_url", "https://api.openai.com/v1")
    else:
        raise ValueError("Unsupported embedding provider.")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("Embedding provider endpoint is not configured.")
    normalized_endpoint = endpoint.strip().rstrip("/")
    return f"{provider}|{normalized_endpoint}|{model.strip()}"
