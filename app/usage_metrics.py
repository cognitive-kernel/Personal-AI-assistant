"""Best-effort per-task Ollama usage telemetry for learning value analysis."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import Any, Iterator

from .db import learning_connection

logger = logging.getLogger("personal_ai_assistant.usage_metrics")
_CURRENT_TASK_ID: ContextVar[int | None] = ContextVar("usage_metrics_task_id", default=None)
_PURPOSES = {"chat", "learning", "programming", "factual_review", "research_plan"}
_MAX_SQLITE_INT = (1 << 63) - 1


@contextmanager
def capture_task(task_id: int) -> Iterator[None]:
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
        raise ValueError("task_id must be a positive integer.")
    token = _CURRENT_TASK_ID.set(task_id)
    try:
        yield
    finally:
        _CURRENT_TASK_ID.reset(token)


def current_task_id() -> int | None:
    return _CURRENT_TASK_ID.get()


def _metric(response: dict[str, Any], key: str) -> int:
    value = response.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return min(value, _MAX_SQLITE_INT)


def record_ollama_response(*, purpose: str, model: str, response: dict[str, Any]) -> bool:
    """Persist numeric usage metadata for the active task without blocking inference."""
    task_id = current_task_id()
    if task_id is None:
        return False
    if purpose not in _PURPOSES or not isinstance(model, str) or not model.strip() or len(model) > 200:
        return False
    if not isinstance(response, dict):
        return False
    values = {
        "prompt_tokens": _metric(response, "prompt_eval_count"),
        "completion_tokens": _metric(response, "eval_count"),
        "total_duration_ns": _metric(response, "total_duration"),
        "load_duration_ns": _metric(response, "load_duration"),
        "prompt_eval_duration_ns": _metric(response, "prompt_eval_duration"),
        "eval_duration_ns": _metric(response, "eval_duration"),
    }
    if not any(values.values()):
        return False
    try:
        with learning_connection() as connection:
            connection.execute(
                """INSERT INTO model_usage_events(
                       task_id, purpose, model_name, prompt_tokens, completion_tokens,
                       total_duration_ns, load_duration_ns, prompt_eval_duration_ns,
                       eval_duration_ns
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, purpose, model.strip(), values["prompt_tokens"],
                    values["completion_tokens"], values["total_duration_ns"],
                    values["load_duration_ns"], values["prompt_eval_duration_ns"],
                    values["eval_duration_ns"],
                ),
            )
        return True
    except Exception as exc:
        logger.warning("Model usage telemetry could not be stored (%s).", type(exc).__name__)
        return False
