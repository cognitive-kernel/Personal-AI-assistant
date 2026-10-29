"""Durable local chat conversations stored in the versioned settings database."""
from __future__ import annotations

from typing import Any

from . import db


def create_conversation() -> dict[str, Any]:
    with db.settings_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO chat_conversations(title) VALUES (?)",
            ("گفتگوی جدید",),
        )
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def list_conversations(*, limit: int = 100) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    with db.settings_connection() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at,
                      (SELECT m.content FROM chat_messages m
                       WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_message,
                      (SELECT COUNT(*) FROM chat_messages m
                       WHERE m.conversation_id = c.id) AS message_count
               FROM chat_conversations c
               ORDER BY c.updated_at DESC, c.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(conversation_id: int) -> dict[str, Any] | None:
    with db.settings_connection() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def delete_conversation(conversation_id: int) -> bool:
    """Delete one conversation and its messages; leave all other conversations intact."""
    with db.settings_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM chat_conversations WHERE id = ?", (conversation_id,)
        )
        return cursor.rowcount > 0


def list_messages(conversation_id: int) -> list[dict[str, Any]]:
    with db.settings_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(conversation_id)
        rows = conn.execute(
            """SELECT m.id, m.conversation_id, m.role, m.content, m.task_id, m.created_at,
                      f.rating AS feedback_rating
               FROM chat_messages AS m
               LEFT JOIN chat_message_feedback AS f ON f.message_id = m.id
               WHERE m.conversation_id = ? ORDER BY m.id""",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_history(conversation_id: int, *, limit: int = 30) -> list[dict[str, str]]:
    with db.settings_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(conversation_id)
        rows = conn.execute(
            """SELECT role, content FROM (
                   SELECT id, role, content FROM chat_messages
                   WHERE conversation_id = ? ORDER BY id DESC LIMIT ?
               ) ORDER BY id""",
            (conversation_id, limit),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"][:5000]} for row in rows]


def append_user_message(conversation_id: int, content: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip() or len(content) > 12000:
        raise ValueError("Chat message must contain 1–12000 non-whitespace characters.")
    with db.settings_connection() as conn:
        conversation = conn.execute(
            "SELECT id, title FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise KeyError(conversation_id)
        cursor = conn.execute(
            """INSERT INTO chat_messages(conversation_id, role, content)
               VALUES (?, 'user', ?)""",
            (conversation_id, content.strip()),
        )
        title = conversation["title"]
        prior_count = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        if prior_count == 1 or title == "گفتگوی جدید":
            title = " ".join(content.strip().split())[:80] or "گفتگوی جدید"
        conn.execute(
            """UPDATE chat_conversations SET title = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (title, conversation_id),
        )
        row = conn.execute(
            """SELECT id, conversation_id, role, content, task_id, created_at
               FROM chat_messages WHERE id = ?""",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def append_assistant_message(
    conversation_id: int, content: str, *, task_id: int | None = None
) -> dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Assistant response must not be empty.")
    with db.settings_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone() is None:
            raise KeyError(conversation_id)
        if task_id is not None:
            conn.execute(
                """INSERT OR IGNORE INTO chat_messages(conversation_id, role, content, task_id)
                   VALUES (?, 'assistant', ?, ?)""",
                (conversation_id, content[:50000], task_id),
            )
            row = conn.execute(
                """SELECT id, conversation_id, role, content, task_id, created_at
                   FROM chat_messages WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
        else:
            cursor = conn.execute(
                """INSERT INTO chat_messages(conversation_id, role, content)
                   VALUES (?, 'assistant', ?)""",
                (conversation_id, content[:50000]),
            )
            row = conn.execute(
                """SELECT id, conversation_id, role, content, task_id, created_at
                   FROM chat_messages WHERE id = ?""",
                (cursor.lastrowid,),
            ).fetchone()
        conn.execute(
            "UPDATE chat_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )
    return dict(row) if row is not None else None
