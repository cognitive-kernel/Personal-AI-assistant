"""Local, provenance-preserving staging of assistant answers as unverified learning candidates.

Candidates are never promoted to trusted knowledge automatically and are not sent to
any external service by this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .db import learning_connection

_MIN_CANDIDATE_CHARS = 80
_MAX_CANDIDATE_CHARS = 50000
_SENSITIVE_VALUE = re.compile(
    r"(?i)\\b(?:password|passphrase|passcode|api[_ -]?key|secret|access[_ -]?token|"
    r"refresh[_ -]?token|private[_ -]?key)\\b\\s*[:=]\\s*[^\\s,;]{6,}"
)


def capture_chat_answer(
    *,
    conversation_id: int,
    message_id: int,
    user_message_id: int,
    task_id: int | None,
    user_query: str,
    answer: str,
    created_at: str,
) -> dict[str, Any] | None:
    """Persist one assistant answer as an unverified candidate, idempotently."""
    for value in (conversation_id, message_id, user_message_id):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("Conversation and message identifiers must be positive integers.")
    if task_id is not None and (
        isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1
    ):
        raise ValueError("task_id must be a positive integer or null.")
    if not isinstance(user_query, str) or not user_query.strip() or len(user_query) > 12000:
        return None
    if not isinstance(answer, str) or not answer.strip() or len(answer) > _MAX_CANDIDATE_CHARS:
        return None
    query = " ".join(user_query.split())
    text = answer.strip()
    if len(text) < _MIN_CANDIDATE_CHARS:
        return None
    # Do not duplicate obvious credential-bearing exchanges into the learning store.
    if _SENSITIVE_VALUE.search(query) or _SENSITIVE_VALUE.search(text):
        return None

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source_kind = "research_answer" if task_id is not None else "chat_answer"
    provenance = {
        "source": source_kind,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "user_message_id": user_message_id,
        "task_id": task_id,
        "user_query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "answer_sha256": digest,
        "created_at": str(created_at)[:40],
        "trust": "unverified",
        "storage": "local_only",
    }
    with learning_connection() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO learning_chat_candidates(
                   conversation_id, message_id, user_message_id, task_id, source_kind,
                   topic_hint, content, content_sha256, provenance_json, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified')""",
            (
                conversation_id, message_id, user_message_id, task_id, source_kind,
                query[:300], text, digest,
                json.dumps(provenance, ensure_ascii=False, sort_keys=True),
            ),
        )
        row = connection.execute(
            """SELECT id, conversation_id, message_id, user_message_id, task_id,
                      source_kind, topic_hint, content, content_sha256,
                      provenance_json, status, created_at
               FROM learning_chat_candidates
               WHERE conversation_id=? AND message_id=?""",
            (conversation_id, message_id),
        ).fetchone()
    return dict(row) if row is not None else None


def list_chat_candidates(*, status: str = "unverified", limit: int = 100) -> list[dict[str, Any]]:
    """List locally staged candidates without changing their trust status."""
    if status not in {"unverified", "reviewed", "promoted", "dismissed"}:
        raise ValueError("Unsupported candidate status.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT id, conversation_id, message_id, user_message_id, task_id,
                      source_kind, topic_hint, content, content_sha256,
                      provenance_json, status, created_at
               FROM learning_chat_candidates
               WHERE status=? ORDER BY created_at DESC, id DESC LIMIT ?""",
            (status, limit),
        ).fetchall()
    return [dict(row) for row in rows]
