"""Persistent user feedback for one-person adaptive response behavior.

Feedback is a preference/quality signal, never factual evidence and never auto-verifies
knowledge. It is retrieved by lexical relevance and injected as bounded guidance.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .db import learning_connection


def init_feedback_table() -> None:
    with learning_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            answer TEXT NOT NULL,
            rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')),
            correction TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedback_rating ON chat_feedback(rating, id DESC)")


def record_feedback(
    query: str, answer: str, rating: str, correction: str = ""
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip() or len(query) > 12000:
        raise ValueError("query must contain 1–12000 characters.")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 20000:
        raise ValueError("answer must contain 1–20000 characters.")
    if rating not in {"helpful", "unhelpful"}:
        raise ValueError("rating must be helpful or unhelpful.")
    if not isinstance(correction, str) or len(correction) > 8000:
        raise ValueError("correction must be at most 8000 characters.")
    init_feedback_table()
    with learning_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chat_feedback(query, answer, rating, correction) VALUES (?, ?, ?, ?)",
            (query.strip(), answer.strip(), rating, correction.strip()),
        )
        feedback_id = int(cur.lastrowid)
    return get_feedback(feedback_id)


def get_feedback(feedback_id: int) -> dict[str, Any]:
    init_feedback_table()
    with learning_connection() as conn:
        row = conn.execute("SELECT * FROM chat_feedback WHERE id=?", (int(feedback_id),)).fetchone()
    if row is None:
        raise KeyError(feedback_id)
    return dict(row)


def list_feedback(limit: int = 50) -> list[dict[str, Any]]:
    init_feedback_table()
    with learning_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_feedback ORDER BY id DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


def search_relevant_feedback(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Find feedback by lexical overlap; do not promote feedback into factual knowledge."""
    if not isinstance(query, str) or not query.strip():
        return []
    init_feedback_table()
    tokens = set(re.findall(r"[\w-]{2,}", query.casefold(), flags=re.UNICODE))
    if not tokens:
        return []
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for item in list_feedback(limit=200):
        searchable = " ".join((item["query"], item["correction"], item["answer"][:1000])).casefold()
        item_tokens = set(re.findall(r"[\w-]{2,}", searchable, flags=re.UNICODE))
        overlap = tokens & item_tokens
        if not overlap:
            continue
        score = len(overlap) / (len(tokens) ** 0.5)
        ranked.append((score, int(item["id"]), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [item for _, _, item in ranked[:max(1, min(10, int(limit)))]]


def feedback_prompt_context(query: str, limit: int = 3) -> str:
    items = search_relevant_feedback(query, limit=limit)
    if not items:
        return ""
    lines = []
    for item in items:
        if item["rating"] == "helpful":
            lines.append(
                f"[Prior user feedback: helpful style signal; not factual evidence]\n"
                f"Request: {item['query'][:500]}\nCorrection/preference: {item['correction'][:500]}"
            )
        else:
            lines.append(
                f"[Prior user feedback: unhelpful answer; warning only, not factual evidence]\n"
                f"Request: {item['query'][:500]}\nCorrection/preference: {item['correction'][:1000]}"
            )
    return "\n\n".join(lines)
