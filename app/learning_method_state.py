"""Shared contracts and state helpers for sequential learning methods."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .db import learning_connection

_ATTEMPT_KEY = re.compile(r"^path-topic:(\d+):method:([a-z_]+):attempt:(\d+)$")


def _assert_task_context(connection, task_context: Any | None) -> None:
    """Fence a domain transition with the exact durable task-queue lease."""
    if task_context is None:
        return
    if callable(getattr(task_context, "assert_current", None)):
        task_context.assert_current(connection)
        return
    from . import task_manager

    task_manager.assert_task_owner(
        connection,
        int(task_context.task_id),
        str(task_context.worker_id),
        int(task_context.lease_epoch),
    )

def _method_attempt_identity(job: dict[str, Any]) -> tuple[int, str, int] | None:
    key = job.get("method_attempt_key")
    if not isinstance(key, str):
        return None
    match = _ATTEMPT_KEY.fullmatch(key)
    if not match:
        return None
    return int(match.group(1)), match.group(2), int(match.group(3))

def _content_hash(job: dict[str, Any]) -> str | None:
    lesson = job.get("lesson")
    if not isinstance(lesson, str) or not lesson.strip():
        return None
    return hashlib.sha256(lesson.encode("utf-8")).hexdigest()

def _method_enabled(method: str) -> bool:
    from .db import get_settings

    settings = get_settings()
    key = {
        "ai_learning": "ai_learning_enabled",
        "basic_learning": "basic_learning_enabled",
        "topic_learning": "topic_learning_enabled",
    }[method]
    return settings.get(key, True) is True

def _topic_context(path_topic_id: int) -> tuple[str, int]:
    with learning_connection() as connection:
        row = connection.execute(
            "SELECT topic, path_id FROM learning_path_topics WHERE id=?",
            (path_topic_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"Learning path topic {path_topic_id} does not exist.")
    return str(row["topic"]), int(row["path_id"])

def _find_job_by_method_key(method_key: str) -> dict[str, Any] | None:
    from . import learning

    with learning_connection() as connection:
        row = connection.execute(
            "SELECT id FROM learning_jobs WHERE method_attempt_key=?",
            (method_key,),
        ).fetchone()
    return learning.get_job(int(row["id"])) if row is not None else None

def _block_topic(
    path_topic_id: int,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE learning_topic_quality_gates
               SET state='blocked', diagnosis_json=?, next_action='manual_review_required',
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=?""",
            (
                json.dumps({"reason": reason, "evidence": evidence or {}}, ensure_ascii=False, sort_keys=True),
                path_topic_id,
            ),
        )
        connection.execute(
            """UPDATE learning_path_topics SET status='needs_review', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status!='completed'""",
            (path_topic_id,),
        )
    return {"status": "blocked", "path_topic_id": path_topic_id, "reason": reason}
