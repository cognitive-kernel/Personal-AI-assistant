"""Explicit user feedback for retrieval ranking; feedback never verifies records."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import init_databases, learning_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_experience_status(experience_id: int) -> str:
    """Read only the fields needed here, avoiding a dependency on app.experience."""
    init_databases()
    with learning_connection() as connection:
        row = connection.execute(
            "SELECT status FROM learning_experiences WHERE id=?",
            (experience_id,),
        ).fetchone()
    if row is None:
        raise KeyError(experience_id)
    return str(row["status"])


def set_feedback(experience_id: int, rating: int) -> dict[str, Any]:
    if rating not in (-1, 1) or isinstance(rating, bool):
        raise ValueError("Feedback rating must be -1 or 1.")
    if _require_experience_status(experience_id) != "verified":
        raise ValueError("Feedback can only be recorded for a verified experience.")
    updated_at = _now()
    with learning_connection() as connection:
        connection.execute(
            """INSERT INTO learning_experience_feedback(experience_id, rating, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(experience_id) DO UPDATE SET
                   rating=excluded.rating, updated_at=excluded.updated_at""",
            (experience_id, rating, updated_at),
        )
        row = connection.execute(
            "SELECT experience_id, rating, updated_at FROM learning_experience_feedback WHERE experience_id=?",
            (experience_id,),
        ).fetchone()
    return dict(row)


def get_feedback_map(experience_ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """Return explicit feedback for selected records, or all feedback when omitted."""
    init_databases()
    with learning_connection() as connection:
        if experience_ids is None:
            rows = connection.execute(
                "SELECT experience_id, rating, updated_at FROM learning_experience_feedback"
            ).fetchall()
        else:
            ids = sorted({int(value) for value in experience_ids})
            if not ids:
                return {}
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT experience_id, rating, updated_at FROM learning_experience_feedback WHERE experience_id IN ({placeholders})",
                ids,
            ).fetchall()
    return {
        int(row["experience_id"]): {
            "rating": int(row["rating"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    }


def delete_feedback(experience_id: int) -> bool:
    _require_experience_status(experience_id)
    init_databases()
    with learning_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM learning_experience_feedback WHERE experience_id=?",
            (experience_id,),
        )
    return cursor.rowcount > 0
