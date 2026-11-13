"""Provider-neutral request adapter for method-scoped lesson generation."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_ALLOWED_PROFILES = frozenset({"ai_learning", "learning", "topic_learning"})


class LearningModelContractError(ValueError):
    """Raised when a method-scoped model request violates its contract."""


def request_lesson_json(
    request: Callable[..., dict[str, Any]],
    *,
    provider_purpose: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    context_tokens: int,
    cpu_threads: int,
    timeout_seconds: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    """Dispatch a JSON lesson request using the selected method's provider profile."""
    if provider_purpose not in _ALLOWED_PROFILES:
        raise LearningModelContractError("Unsupported learning provider profile.")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise LearningModelContractError("A valid model name is required.")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise LearningModelContractError("A non-empty system prompt is required.")
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        raise LearningModelContractError("A non-empty user prompt is required.")
    if isinstance(context_tokens, bool) or not isinstance(context_tokens, int) or not 512 <= context_tokens <= 1048576:
        raise LearningModelContractError("context_tokens must be between 512 and 1048576.")
    if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or not 0 <= cpu_threads <= 64:
        raise LearningModelContractError("cpu_threads must be between 0 and 64.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 5 <= timeout_seconds <= 600:
        raise LearningModelContractError("timeout_seconds must be between 5 and 600.")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 8192:
        raise LearningModelContractError("max_output_tokens must be between 1 and 8192.")

    return request(
        "/api/chat",
        {
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "num_ctx": context_tokens,
                "num_thread": cpu_threads,
                "num_predict": max_output_tokens,
            },
        },
        timeout=timeout_seconds,
        purpose=provider_purpose,
    )
