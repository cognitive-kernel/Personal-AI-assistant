"""Optional adapter between the isolated knowledge store and existing chat research."""
from __future__ import annotations
from typing import Any
from .config import retrieval_is_enabled
from .db import initialize_store
from .service import search_knowledge, record_query_trace

MAX_CONTEXT_CHARS = 5_000
MAX_CHUNK_CHARS = 1_200
MAX_RESULTS = 5

def retrieve_context(query: str) -> list[dict[str, Any]]:
    """Return bounded, traceable evidence only when the retrieval gate is enabled."""
    if not retrieval_is_enabled():
        return []
    initialize_store()
    result = search_knowledge(query, limit=MAX_RESULTS)
    record_query_trace(query, [int(row["chunk_id"]) for row in result["items"]])
    items: list[dict[str, Any]] = []
    used = 0
    for row in result["items"]:
        body = str(row["body"])[:MAX_CHUNK_CHARS]
        remaining = MAX_CONTEXT_CHARS - used
        if remaining <= 0:
            break
        body = body[:remaining]
        if not body:
            continue
        items.append({
            "course_title": row["course_title"],
            "topic_title": row["topic_title"],
            "lesson_title": row["lesson_title"],
            "content_version_id": row["content_version_id"],
            "version_no": row["version_no"],
            "chunk_id": row["chunk_id"],
            "heading_path": row["heading_path"],
            "text": body,
        })
        used += len(body)
    return items
