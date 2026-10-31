"""Queue handler for bounded multi-source temporary knowledge validation."""
from __future__ import annotations

from typing import Any

from . import knowledge_lifecycle
from .task_worker import TaskContext

KNOWLEDGE_VALIDATE_TASK_TYPE = "learning.validate_candidate"
_ALLOWED_KEYS = {"candidate_id", "candidate_sha256"}


def handle_validate_candidate(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_KEYS:
        raise ValueError("Knowledge validation payload has an unsupported schema.")
    candidate_id = payload.get("candidate_id")
    candidate_sha256 = payload.get("candidate_sha256")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(candidate_sha256, str) or len(candidate_sha256) != 64:
        raise ValueError("candidate_sha256 must be a SHA-256 digest.")
    context.checkpoint({"stage": "starting", "candidate_id": candidate_id})
    readiness = knowledge_lifecycle.candidate_readiness(candidate_id)
    if readiness["candidate_sha256"] != candidate_sha256:
        raise ValueError("Candidate content changed after the validation task was queued.")
    result = knowledge_lifecycle.validate_candidate_with_models(candidate_id, context=context)
    if getattr(context, "completed_task", None) is None:
        context.checkpoint({"stage": "finished", "candidate_id": candidate_id})
    return result
