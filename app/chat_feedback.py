"""Explicit per-response feedback; ratings are signals and never verify knowledge."""
from __future__ import annotations

from typing import Any

from . import db


def set_message_feedback(conversation_id: int, message_id: int, rating: int) -> dict[str, Any]:
    """Set or replace a rating for one assistant message in its owning conversation."""
    if isinstance(rating, bool) or not isinstance(rating, int) or rating not in (-1, 1):
        raise ValueError("Feedback rating must be -1 or 1.")
    if isinstance(conversation_id, bool) or not isinstance(conversation_id, int) or conversation_id < 1:
        raise ValueError("Conversation ID must be a positive integer.")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
        raise ValueError("Message ID must be a positive integer.")

    with db.settings_connection() as connection:
        message = connection.execute(
            "SELECT role FROM chat_messages WHERE id=? AND conversation_id=?",
            (message_id, conversation_id),
        ).fetchone()
        if message is None:
            raise KeyError(message_id)
        if message["role"] != "assistant":
            raise ValueError("Feedback can only be recorded for assistant responses.")
        connection.execute(
            """INSERT INTO chat_message_feedback(message_id, rating, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(message_id) DO UPDATE SET
                   rating=excluded.rating, updated_at=CURRENT_TIMESTAMP""",
            (message_id, rating),
        )
        row = connection.execute(
            "SELECT message_id, rating, updated_at FROM chat_message_feedback WHERE message_id=?",
            (message_id,),
        ).fetchone()
    return {
        "conversation_id": conversation_id,
        "message_id": int(row["message_id"]),
        "rating": int(row["rating"]),
        "updated_at": str(row["updated_at"]),
    }
