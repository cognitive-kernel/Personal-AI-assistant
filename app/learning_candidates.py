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

_MIN_CANDIDATE_CHARS = 1
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:password|passphrase|passcode|api[_ -]?key|secret|access[_ -]?token|"
    r"refresh[_ -]?token|private[_ -]?key)\b\s*[:=]\s*[^\s,;]{6,}"
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
    if not isinstance(answer, str) or not answer.strip():
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


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CANDIDATE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "how", "what", "when", "where",
    "why", "can", "could", "should", "would", "please", "show", "explain", "help",
    "use", "using", "about", "this", "that", "are", "was", "were", "is", "be",
    "در", "برای", "با", "از", "به", "را", "که", "چطور", "چگونه", "چیست", "است",
    "میخواهم", "میخوام", "لطفا", "لطفاً", "آموزش", "مثال", "توضیح",
})


def _topic_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if len(token) >= 2 and token.casefold() not in _CANDIDATE_STOPWORDS
    }


def relevant_chat_candidates(
    topic: str, *, max_candidates: int = 12, max_chars: int = 24000,
    cursor: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Page through every matching unverified chat observation without dropping long answers.

    The cursor is stable across task checkpoints. A long answer is split into bounded
    content chunks before the next candidate is visited; all omitted observations
    remain eligible for a later page and keep their original unverified status.
    """
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 300:
        raise ValueError("topic must contain 1–300 characters.")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer.")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1000:
        raise ValueError("max_chars must be at least 1000.")
    if cursor is None:
        candidate_id, content_offset = 1, 0
    else:
        if (
            not isinstance(cursor, dict)
            or set(cursor) != {"candidate_id", "content_offset"}
            or any(
                isinstance(cursor.get(key), bool)
                or not isinstance(cursor.get(key), int)
                or cursor[key] < (1 if key == "candidate_id" else 0)
                for key in ("candidate_id", "content_offset")
            )
        ):
            raise ValueError("candidate cursor is invalid.")
        candidate_id = cursor["candidate_id"]
        content_offset = cursor["content_offset"]

    tokens = _topic_tokens(topic)
    if not tokens:
        return {
            "items": [], "matched_count": 0, "included_count": 0,
            "omitted_count": 0, "has_more": False, "next_cursor": None,
            "context_truncated": False,
        }
    hint_conditions = " OR ".join("topic_hint LIKE ?" for _ in tokens)
    content_conditions = " OR ".join("content LIKE ?" for _ in tokens)
    patterns = [f"%{token}%" for token in sorted(tokens)]
    with learning_connection() as connection:
        rows = connection.execute(
            f"""SELECT id, source_kind, topic_hint, content, content_sha256,
                       provenance_json, created_at
                FROM learning_chat_candidates
                WHERE status='unverified' AND id>=?
                  AND (({hint_conditions}) OR ({content_conditions}))
                ORDER BY id ASC LIMIT ?""",
            (candidate_id, *patterns, *patterns, max_candidates + 1),
        ).fetchall()

    items: list[dict[str, Any]] = []
    used_chars = 0
    next_cursor: dict[str, int] | None = None
    for row in rows:
        row_id = int(row["id"])
        text = str(row["content"])
        start_offset = content_offset if row_id == candidate_id else 0
        if start_offset >= len(text):
            next_cursor = {"candidate_id": row_id + 1, "content_offset": 0}
            content_offset = 0
            continue
        remaining = max_chars - used_chars
        if len(items) >= max_candidates or remaining <= 0:
            next_cursor = {"candidate_id": row_id, "content_offset": start_offset}
            break
        content_chunk = text[start_offset:start_offset + remaining]
        if not content_chunk:
            next_cursor = {"candidate_id": row_id, "content_offset": start_offset}
            break
        hint = str(row["topic_hint"] or "")
        overlap = len(tokens & (_topic_tokens(hint) | _topic_tokens(text)))
        exact_phrase = " ".join(topic.casefold().split()) in " ".join(hint.casefold().split())
        try:
            provenance = json.loads(row["provenance_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            provenance = {"trust": "unverified", "storage": "local_only"}
        safe_provenance = provenance if isinstance(provenance, dict) else {}
        safe_provenance = {
            key: safe_provenance[key]
            for key in ("source", "answer_sha256", "created_at", "trust", "storage")
            if key in safe_provenance
        }
        chunk_end = start_offset + len(content_chunk)
        is_truncated = chunk_end < len(text)
        items.append({
            "candidate_id": row_id,
            "source_kind": str(row["source_kind"]),
            "topic_hint": hint,
            "content": content_chunk,
            "content_sha256": str(row["content_sha256"]),
            "provenance": safe_provenance,
            "status": "unverified",
            "created_at": str(row["created_at"]),
            "relevance_score": overlap * 10 + (100 if exact_phrase else 0),
            "content_truncated": is_truncated,
            "content_offset": start_offset,
        })
        used_chars += len(content_chunk)
        if is_truncated:
            next_cursor = {"candidate_id": row_id, "content_offset": chunk_end}
            break
        next_cursor = {"candidate_id": row_id + 1, "content_offset": 0}
    else:
        next_cursor = None

    has_more = next_cursor is not None
    return {
        "items": items,
        "matched_count": len(rows),
        "matched_count_is_page_window": True,
        "included_count": len(items),
        "omitted_count": max(0, len(rows) - len(items)),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "context_truncated": has_more and used_chars >= max_chars,
    }
