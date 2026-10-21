"""Persistent task handler for bounded local research chat."""
from __future__ import annotations

import hashlib
from typing import Any

from . import chat_history, chat_research
from .task_worker import PermanentTaskError, TaskContext

RESEARCH_TASK_TYPE = "research.answer"
_ALLOWED_KEYS = {"message", "history", "conversation_id"}


def validate_payload(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_KEYS:
        raise ValueError("Research task payload contains unsupported fields.")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 12000:
        raise ValueError("message must contain 1–12000 characters.")
    history = payload.get("history", [])
    if not isinstance(history, list) or len(history) > 30:
        raise ValueError("history must be a list containing at most 30 messages.")
    normalized: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ValueError("Each history item must contain only role and content.")
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ValueError("History role must be user or assistant.")
        if not isinstance(content, str) or len(content) > 5000:
            raise ValueError("History content must be text up to 5000 characters.")
        normalized.append({"role": role, "content": content})
    return message, normalized


def handle_research_task(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    try:
        message, history = validate_payload(payload)
    except (ValueError, TypeError, KeyError) as exc:
        raise PermanentTaskError(str(exc)) from exc
    conversation_id = payload.get("conversation_id")
    if conversation_id is not None and (isinstance(conversation_id, bool) or not isinstance(conversation_id, int) or conversation_id < 1):
        raise PermanentTaskError("conversation_id must be a positive integer when provided.")
    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

    def save_phase(state: dict[str, Any]) -> None:
        # Store only phase metadata and a digest, never raw user text, in checkpoints.
        context.checkpoint(
            {
                "phase": str(state.get("phase", "research")),
                "message_sha256": message_hash,
                **{
                    key: value
                    for key, value in state.items()
                    if key == "source_count" or key == "review_passed"
                },
            }
        )

    context.checkpoint({"phase": "research_task_started", "message_sha256": message_hash})
    reply = chat_research.answer(message, history, checkpoint=save_phase)
    context.checkpoint({"phase": "research_task_completed", "message_sha256": message_hash})
    if conversation_id is not None:
        chat_history.append_assistant_message(conversation_id, reply, task_id=context.task_id)
    return {"reply": reply, "message_sha256": message_hash}
