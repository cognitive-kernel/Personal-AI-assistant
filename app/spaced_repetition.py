"""Deterministic spaced-repetition scheduling for curriculum topics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .db import init_databases, learning_connection


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _topic_exists(path_id: int, topic_id: int) -> None:
    init_databases()
    with learning_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM learning_path_topics WHERE id=? AND path_id=?",
            (topic_id, path_id),
        ).fetchone()
    if row is None:
        raise KeyError(topic_id)


def record_review(path_id: int, topic_id: int, rating: int) -> dict[str, Any]:
    """Record a 0-5 recall rating and update the deterministic SM-2-style schedule."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
           for value in (path_id, topic_id)):
        raise ValueError("path_id and topic_id must be positive integers.")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 0 <= rating <= 5:
        raise ValueError("rating must be an integer from 0 through 5.")
    _topic_exists(path_id, topic_id)
    now = _now()
    reviewed_at = _iso(now)
    with learning_connection() as connection:
        previous = connection.execute(
            "SELECT * FROM learning_review_schedule WHERE topic_id=?",
            (topic_id,),
        ).fetchone()
        previous_repetitions = int(previous["repetitions"]) if previous else 0
        previous_interval = int(previous["interval_days"]) if previous else 0
        previous_ease = float(previous["ease_factor"]) if previous else 2.5

        if rating < 3:
            repetitions = 0
            interval_days = 1
            ease_factor = max(1.3, previous_ease - 0.2)
        else:
            repetitions = previous_repetitions + 1
            if previous_repetitions == 0:
                interval_days = 1
            elif previous_repetitions == 1:
                interval_days = 6
            else:
                interval_days = max(1, round(previous_interval * previous_ease))
            difficulty = 5 - rating
            ease_factor = max(
                1.3,
                previous_ease + (0.1 - difficulty * (0.08 + difficulty * 0.02)),
            )
        due_at = _iso(now + timedelta(days=interval_days))
        connection.execute(
            """INSERT INTO learning_review_schedule(
                   topic_id, repetitions, interval_days, ease_factor, due_at,
                   last_reviewed_at, last_rating, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET
                   repetitions=excluded.repetitions,
                   interval_days=excluded.interval_days,
                   ease_factor=excluded.ease_factor,
                   due_at=excluded.due_at,
                   last_reviewed_at=excluded.last_reviewed_at,
                   last_rating=excluded.last_rating,
                   updated_at=excluded.updated_at""",
            (topic_id, repetitions, interval_days, ease_factor, due_at, reviewed_at, rating, reviewed_at),
        )
        cursor = connection.execute(
            """INSERT INTO learning_review_events(
                   topic_id, rating, previous_interval_days, interval_days,
                   previous_ease_factor, ease_factor, repetitions, due_at, reviewed_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (topic_id, rating, previous_interval, interval_days, previous_ease,
             ease_factor, repetitions, due_at, reviewed_at),
        )
        event_id = int(cursor.lastrowid)
    return {
        "path_id": path_id,
        "topic_id": topic_id,
        "event_id": event_id,
        "rating": rating,
        "repetitions": repetitions,
        "previous_interval_days": previous_interval,
        "interval_days": interval_days,
        "previous_ease_factor": round(previous_ease, 3),
        "ease_factor": round(ease_factor, 3),
        "last_reviewed_at": reviewed_at,
        "due_at": due_at,
        "note": "زمان‌بندی قطعی بر اساس رأی مرور است؛ این مقدار به‌تنهایی تسلط علمی یا تکمیل سرفصل را اثبات نمی‌کند.",
    }


def list_due_reviews(limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer.")
    safe_limit = max(1, min(100, limit))
    now = _iso(_now())
    init_databases()
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT s.topic_id, t.path_id, p.title AS path_title, p.curriculum_slug,
                      t.topic, t.position, t.status AS topic_status,
                      s.repetitions, s.interval_days, s.ease_factor, s.due_at,
                      s.last_reviewed_at, s.last_rating
               FROM learning_review_schedule AS s
               JOIN learning_path_topics AS t ON t.id=s.topic_id
               JOIN learning_paths AS p ON p.id=t.path_id
               WHERE s.due_at <= ?
               ORDER BY s.due_at ASC, t.path_id ASC, t.position ASC
               LIMIT ?""",
            (now, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_topic_review_history(path_id: int, topic_id: int, limit: int = 30) -> dict[str, Any]:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
           for value in (path_id, topic_id)):
        raise ValueError("path_id and topic_id must be positive integers.")
    _topic_exists(path_id, topic_id)
    safe_limit = max(1, min(100, int(limit)))
    init_databases()
    with learning_connection() as connection:
        schedule = connection.execute(
            "SELECT * FROM learning_review_schedule WHERE topic_id=?",
            (topic_id,),
        ).fetchone()
        events = connection.execute(
            """SELECT id, topic_id, rating, previous_interval_days, interval_days,
                      previous_ease_factor, ease_factor, repetitions, due_at, reviewed_at
               FROM learning_review_events WHERE topic_id=?
               ORDER BY id DESC LIMIT ?""",
            (topic_id, safe_limit),
        ).fetchall()
    return {
        "path_id": path_id,
        "topic_id": topic_id,
        "schedule": dict(schedule) if schedule else None,
        "events": [dict(row) for row in events],
        "count": len(events),
    }
