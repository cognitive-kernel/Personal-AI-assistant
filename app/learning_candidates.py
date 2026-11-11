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
    topic: str, *, max_candidates: int = 12, max_chars: int = 24000
) -> dict[str, Any]:
    """Retrieve matching assistant answers as unverified, local learning observations.

    Retrieval never changes candidate trust status. Per-prompt bounds protect the
    model context; matched candidates beyond those bounds remain stored and visible
    through list_chat_candidates for later topic-specific retrieval.
    """
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 300:
        raise ValueError("topic must contain 1–300 characters.")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer.")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1000:
        raise ValueError("max_chars must be at least 1000.")
    tokens = _topic_tokens(topic)
    if not tokens:
        return {"items": [], "matched_count": 0, "included_count": 0, "omitted_count": 0}
    conditions = " OR ".join("topic_hint LIKE ?" for _ in tokens)
    patterns = [f"%{token}%" for token in sorted(tokens)]
    with learning_connection() as connection:
        rows = connection.execute(
            f"""SELECT id, conversation_id, message_id, user_message_id, task_id,
                       source_kind, topic_hint, content, content_sha256,
                       provenance_json, status, created_at
                FROM learning_chat_candidates
                WHERE status='unverified' AND ({conditions})
                ORDER BY created_at DESC, id DESC""",
            patterns,
        ).fetchall()
    normalized_topic = " ".join(topic.casefold().split())
    ranked: list[tuple[int, Any]] = []
    for row in rows:
        hint = str(row["topic_hint"] or "")
        overlap = len(tokens & _topic_tokens(hint))
        if overlap == 0:
            continue
        exact_phrase = normalized_topic in " ".join(hint.casefold().split())
        ranked.append((overlap * 10 + (100 if exact_phrase else 0), row))
    ranked.sort(key=lambda item: (item[0], str(item[1]["created_at"]), int(item[1]["id"])), reverse=True)
    items: list[dict[str, Any]] = []
    used_chars = 0
    for score, row in ranked:
        if len(items) >= max_candidates:
            break
        content = str(row["content"])
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        included_content = content[:remaining]
        if not included_content:
            continue
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
        items.append({
            "candidate_id": int(row["id"]),
            "source_kind": str(row["source_kind"]),
            "topic_hint": str(row["topic_hint"] or ""),
            "content": included_content,
            "content_sha256": str(row["content_sha256"]),
            "provenance": safe_provenance,
            "status": "unverified",
            "created_at": str(row["created_at"]),
            "relevance_score": score,
            "content_truncated": len(included_content) < len(content),
        })
        used_chars += len(included_content)
    return {
        "items": items,
        "matched_count": len(ranked),
        "included_count": len(items),
        "omitted_count": max(0, len(ranked) - len(items)),
        "context_truncated": used_chars >= max_chars and len(ranked) > len(items),
    }
