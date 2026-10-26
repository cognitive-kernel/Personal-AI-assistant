"""Deterministic, non-autonomous next-step recommendations for learning paths."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .db import init_databases, learning_connection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_learning_recommendations(
    *, limit: int = 10, path_id: int | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Rank due reviews, active topics, weak completed topics, and the next sequential topic.

    This planner only recommends actions; it never starts or mutates learning jobs.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer from 1 to 50.")
    if path_id is not None and (
        isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1
    ):
        raise ValueError("path_id must be a positive integer.")
    instant = now or _now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    now_iso = _iso(instant)

    init_databases()
    with learning_connection() as connection:
        if path_id is not None and connection.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone() is None:
            raise KeyError(path_id)
        sql = """SELECT t.id AS topic_id, t.path_id, p.title AS path_title,
                        p.curriculum_slug, t.topic, t.position, t.status,
                        t.latest_score, t.updated_at
                 FROM learning_path_topics AS t
                 JOIN learning_paths AS p ON p.id=t.path_id"""
        params: tuple[Any, ...] = ()
        if path_id is not None:
            sql += " WHERE t.path_id=?"
            params = (path_id,)
        sql += " ORDER BY t.path_id ASC, t.position ASC, t.id ASC"
        topics = [dict(row) for row in connection.execute(sql, params).fetchall()]
        schedules = {
            int(row["topic_id"]): dict(row)
            for row in connection.execute(
                """SELECT topic_id, due_at, repetitions, interval_days, last_rating
                   FROM learning_review_schedule"""
            ).fetchall()
        }
        semantic_rows = connection.execute(
            """SELECT j.path_topic_id AS topic_id, a.id AS attempt_id, r.review_json
               FROM learning_jobs j
               JOIN assessment_attempts a ON a.job_id=j.id
               JOIN assessment_semantic_reviews r ON r.attempt_id=a.id
               WHERE j.path_topic_id IS NOT NULL
                 AND a.id=(
                    SELECT MAX(a2.id) FROM assessment_attempts a2
                    JOIN learning_jobs j2 ON j2.id=a2.job_id
                    WHERE j2.path_topic_id=j.path_topic_id
                 )
               ORDER BY r.id DESC"""
        ).fetchall()

    semantic_gaps: dict[int, dict[str, Any]] = {}
    for review_row in semantic_rows:
        topic_key = int(review_row["topic_id"])
        if topic_key in semantic_gaps:
            continue
        try:
            stored = json.loads(review_row["review_json"])
            review = stored.get("review", {}) if isinstance(stored, dict) else {}
            items = review.get("items", []) if isinstance(review, dict) else []
        except (TypeError, json.JSONDecodeError):
            continue
        if not (
            isinstance(stored, dict)
            and stored.get("independent_evaluator") is True
            and stored.get("independent_verification") is False
            and isinstance(stored.get("evaluator_digest"), str)
            and isinstance(items, list)
        ):
            continue
        counts = {"partial": 0, "incorrect": 0, "uncertain": 0}
        missing: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            judgment = item.get("judgment")
            if judgment in counts:
                counts[judgment] += 1
            concepts = item.get("missing_concepts", [])
            if isinstance(concepts, list):
                missing.extend(value.strip() for value in concepts if isinstance(value, str) and value.strip())
        if any(counts.values()):
            semantic_gaps[topic_key] = {
                **counts,
                "missing_concepts": list(dict.fromkeys(missing))[:12],
                "review_attempt_id": int(review_row["attempt_id"]),
                "rubric_version": stored.get("rubric_version"),
                "evaluator_digest": stored.get("evaluator_digest"),
                "advisory_only": True,
            }

    due_by_topic: dict[int, dict[str, Any]] = {}
    for item in topics:
        topic_id = int(item["topic_id"])
        schedule = schedules.get(topic_id)
        due_at = str(schedule.get("due_at") or "") if schedule else ""
        if due_at and due_at <= now_iso:
            due_by_topic[topic_id] = {
                **item,
                "due_at": due_at,
                "repetitions": schedule.get("repetitions", 0),
                "interval_days": schedule.get("interval_days", 0),
                "last_rating": schedule.get("last_rating"),
            }
    recommendations: list[dict[str, Any]] = []
    seen: set[int] = set()

    for item in topics:
        topic_id = int(item["topic_id"])
        due = due_by_topic.get(topic_id)
        if due is None:
            continue
        due_at = str(due.get("due_at") or "")
        try:
            due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            overdue_days = max(0, int((instant - due_dt.astimezone(timezone.utc)).total_seconds() // 86400))
        except (TypeError, ValueError, OverflowError):
            overdue_days = 0
        recommendations.append({
            **item,
            "recommendation_type": "spaced_repetition",
            "action": "review_topic",
            "priority": 100 + min(overdue_days, 30),
            "reason": "A spaced-repetition review is due.",
            "due_at": due_at,
            "overdue_days": overdue_days,
        })
        seen.add(topic_id)

    by_path: dict[int, list[dict[str, Any]]] = {}
    for item in topics:
        by_path.setdefault(int(item["path_id"]), []).append(item)

    for path_topics in by_path.values():
        for index, item in enumerate(path_topics):
            topic_id = int(item["topic_id"])
            if topic_id in seen:
                continue
            schedule = schedules.get(topic_id)
            if schedule:
                due_at = str(schedule.get("due_at") or "")
                if due_at and due_at > now_iso and not (
                    item["status"] == "in_progress"
                    and item["latest_score"] is not None
                    and float(item["latest_score"]) < 80
                ):
                    # Keep low-scoring active topics visible as gaps even when a review is scheduled.
                    continue
            status = item["status"]
            score = item["latest_score"]
            semantic_gap = semantic_gaps.get(topic_id)
            if semantic_gap and status in {"in_progress", "completed"}:
                gap_weight = 12 * semantic_gap["incorrect"] + 6 * semantic_gap["partial"] + 2 * semantic_gap["uncertain"]
                recommendations.append({
                    **item,
                    "recommendation_type": "semantic_knowledge_gap",
                    "action": "reinforce_topic",
                    "priority": 88 + min(11, gap_weight),
                    "reason": "The latest independent-model semantic review reported incomplete, incorrect, or uncertain answer concepts; this is advisory evidence and does not change the score or completion status.",
                    "semantic_gap": semantic_gap,
                    "due_at": None,
                    "overdue_days": 0,
                })
                seen.add(topic_id)
                continue
            if status == "in_progress":
                if score is not None and float(score) < 80:
                    recommendations.append({
                        **item,
                        "recommendation_type": "knowledge_gap",
                        "action": "reinforce_topic",
                        "priority": 90 + min(20, max(0, int((80 - float(score)) / 4))),
                        "reason": "The latest assessment score is below the 80-point mastery threshold; reinforce this topic before advancing.",
                        "gap_score": round(80 - float(score), 2),
                        "due_at": None,
                        "overdue_days": 0,
                    })
                else:
                    recommendations.append({
                        **item,
                        "recommendation_type": "continue_learning",
                        "action": "continue_topic",
                        "priority": 85,
                        "reason": "Continue the topic already in progress before opening another lesson.",
                        "due_at": None,
                        "overdue_days": 0,
                    })
                seen.add(topic_id)
                continue
            if status == "completed" and score is not None and float(score) < 80:
                recommendations.append({
                    **item,
                    "recommendation_type": "reinforcement",
                    "action": "reinforce_topic",
                    "priority": 70 + min(30, max(0, int((80 - float(score)) / 2))),
                    "reason": "The latest assessment score is below the 80-point mastery threshold; reinforce this topic.",
                    "gap_score": round(80 - float(score), 2),
                    "due_at": None,
                    "overdue_days": 0,
                })
                seen.add(topic_id)

        # Recommend only the first pending topic whose prerequisites are completed.
        for index, item in enumerate(path_topics):
            if item["status"] == "completed":
                continue
            if item["status"] == "in_progress":
                break
            if item["status"] == "pending":
                prior_topics = path_topics[:index]
                if all(previous["status"] == "completed" for previous in prior_topics):
                    topic_id = int(item["topic_id"])
                    if topic_id not in seen:
                        recommendations.append({
                            **item,
                            "recommendation_type": "next_topic",
                            "action": "start_topic",
                            "priority": 50,
                            "reason": "This is the next sequential topic with all earlier topics completed.",
                            "due_at": None,
                            "overdue_days": 0,
                        })
                        seen.add(topic_id)
                break

    recommendations.sort(
        key=lambda item: (
            -int(item["priority"]),
            str(item.get("due_at") or "9999"),
            float(item["latest_score"]) if item.get("latest_score") is not None else 101.0,
            int(item["path_id"]),
            int(item["position"]),
            int(item["topic_id"]),
        )
    )
    return {
        "policy_version": 1,
        "generated_at": _iso(instant),
        "autonomous_action_taken": False,
        "path_id": path_id,
        "recommendations": recommendations[:limit],
    }
