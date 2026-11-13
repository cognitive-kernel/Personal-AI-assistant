"""Read-only Experience System view over durable learning-method outcomes."""
from __future__ import annotations

import json
from typing import Any

from .db import learning_connection


class ExperienceLearningObserverError(ValueError):
    """Raised when an observation query violates its bounded read contract."""


def list_method_observations(
    *,
    path_topic_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read immutable method events without owning or mutating coordinator state."""
    if path_topic_id is not None and (
        isinstance(path_topic_id, bool) or not isinstance(path_topic_id, int) or path_topic_id < 1
    ):
        raise ExperienceLearningObserverError("path_topic_id must be a positive integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ExperienceLearningObserverError("limit must be between 1 and 200.")

    query = """SELECT e.id, e.path_topic_id, e.method, e.attempt_number,
                      e.event_sequence, e.status, e.event_key, e.payload_sha256,
                      e.job_id, e.external_run_ref, e.content_sha256, e.score,
                      e.diagnosis_json, e.evidence_json, e.occurred_at, e.created_at,
                      t.topic, t.path_id, p.title AS path_title
               FROM learning_topic_method_attempt_events e
               JOIN learning_path_topics t ON t.id=e.path_topic_id
               JOIN learning_paths p ON p.id=t.path_id"""
    params: tuple[Any, ...]
    if path_topic_id is not None:
        query += " WHERE e.path_topic_id=?"
        params = (path_topic_id, limit)
    else:
        params = (limit,)
    query += " ORDER BY e.id DESC LIMIT ?"

    with learning_connection() as connection:
        rows = connection.execute(query, params).fetchall()

    observations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for field in ("diagnosis_json", "evidence_json"):
            try:
                parsed = json.loads(item.pop(field))
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            item[field.removesuffix("_json")] = parsed if isinstance(parsed, dict) else {}
        item["observer_role"] = "read_only_experience_observer"
        item["coordinator_state_mutated"] = False
        observations.append(item)
    return observations
