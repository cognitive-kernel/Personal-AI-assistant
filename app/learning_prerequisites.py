"""Explicit, cycle-safe prerequisite DAGs for learning paths."""
from __future__ import annotations

from typing import Any

from .db import init_databases, learning_connection


def set_path_mode(path_id: int, mode: str) -> dict[str, Any]:
    if isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1:
        raise ValueError("path_id must be a positive integer.")
    if mode not in {"linear", "custom"}:
        raise ValueError("mode must be linear or custom.")
    init_databases()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM learning_paths WHERE id=?", (path_id,)).fetchone() is None:
            raise KeyError(path_id)
        connection.execute(
            "UPDATE learning_paths SET prerequisite_mode=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (mode, path_id),
        )
    return {"path_id": path_id, "prerequisite_mode": mode}


def set_topic_prerequisites(path_id: int, topic_id: int, prerequisite_topic_ids: list[int]) -> dict[str, Any]:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (path_id, topic_id)):
        raise ValueError("path_id and topic_id must be positive integers.")
    if not isinstance(prerequisite_topic_ids, list) or len(prerequisite_topic_ids) > 100:
        raise ValueError("prerequisite_topic_ids must be a list of at most 100 IDs.")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in prerequisite_topic_ids):
        raise ValueError("Every prerequisite ID must be a positive integer.")
    if len(set(prerequisite_topic_ids)) != len(prerequisite_topic_ids):
        raise ValueError("Duplicate prerequisite IDs are not allowed.")
    if topic_id in prerequisite_topic_ids:
        raise ValueError("A topic cannot depend on itself.")
    init_databases()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        path = connection.execute("SELECT id FROM learning_paths WHERE id=?", (path_id,)).fetchone()
        if path is None:
            raise KeyError(path_id)
        topic = connection.execute(
            "SELECT id, path_id FROM learning_path_topics WHERE id=?", (topic_id,)
        ).fetchone()
        if topic is None or int(topic["path_id"]) != path_id:
            raise KeyError(topic_id)
        if prerequisite_topic_ids:
            placeholders = ",".join("?" for _ in prerequisite_topic_ids)
            rows = connection.execute(
                f"SELECT id, path_id FROM learning_path_topics WHERE id IN ({placeholders})",
                prerequisite_topic_ids,
            ).fetchall()
            if len(rows) != len(prerequisite_topic_ids) or any(int(row["path_id"]) != path_id for row in rows):
                raise ValueError("Every prerequisite must belong to the same learning path.")
        # Proposed edge topic -> prerequisite is invalid if the prerequisite already
        # reaches topic through existing dependency edges.
        for prerequisite_id in prerequisite_topic_ids:
            reachable = connection.execute(
                """WITH RECURSIVE ancestors(id) AS (
                       SELECT prerequisite_topic_id FROM learning_topic_prerequisites WHERE topic_id=?
                       UNION
                       SELECT p.prerequisite_topic_id
                       FROM learning_topic_prerequisites p JOIN ancestors a ON p.topic_id=a.id
                   )
                   SELECT 1 FROM ancestors WHERE id=? LIMIT 1""",
                (prerequisite_id, topic_id),
            ).fetchone()
            if reachable is not None:
                raise ValueError("Prerequisite update would create a cycle.")
        connection.execute("DELETE FROM learning_topic_prerequisites WHERE topic_id=?", (topic_id,))
        for prerequisite_id in prerequisite_topic_ids:
            connection.execute(
                "INSERT INTO learning_topic_prerequisites(topic_id, prerequisite_topic_id) VALUES (?, ?)",
                (topic_id, prerequisite_id),
            )
        connection.execute(
            "UPDATE learning_paths SET prerequisite_mode='custom', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (path_id,),
        )
    return {
        "path_id": path_id,
        "topic_id": topic_id,
        "prerequisite_mode": "custom",
        "prerequisite_topic_ids": sorted(prerequisite_topic_ids),
    }


def get_topic_prerequisites(path_id: int, topic_id: int) -> dict[str, Any]:
    init_databases()
    with learning_connection() as connection:
        path = connection.execute(
            "SELECT prerequisite_mode FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone()
        if path is None:
            raise KeyError(path_id)
        topic = connection.execute(
            "SELECT id, topic, path_id FROM learning_path_topics WHERE id=?", (topic_id,)
        ).fetchone()
        if topic is None or int(topic["path_id"]) != path_id:
            raise KeyError(topic_id)
        rows = connection.execute(
            """SELECT p.prerequisite_topic_id, t.topic, t.status
               FROM learning_topic_prerequisites p
               JOIN learning_path_topics t ON t.id=p.prerequisite_topic_id
               WHERE p.topic_id=? ORDER BY t.position, t.id""",
            (topic_id,),
        ).fetchall()
    return {
        "path_id": path_id,
        "topic_id": topic_id,
        "topic": topic["topic"],
        "prerequisite_mode": path["prerequisite_mode"],
        "prerequisites": [dict(row) for row in rows],
    }


def blocked_prerequisites(connection, topic_id: int) -> list[dict[str, Any]]:
    """Return incomplete transitive prerequisites under the path's active policy."""
    topic = connection.execute(
        """SELECT t.id, t.path_id, t.position, p.prerequisite_mode
           FROM learning_path_topics t JOIN learning_paths p ON p.id=t.path_id
           WHERE t.id=?""",
        (topic_id,),
    ).fetchone()
    if topic is None:
        raise KeyError(topic_id)
    if topic["prerequisite_mode"] == "linear":
        rows = connection.execute(
            """SELECT id, topic, status FROM learning_path_topics
               WHERE path_id=? AND position<? AND status!='completed'
               ORDER BY position, id""",
            (topic["path_id"], topic["position"]),
        ).fetchall()
    else:
        rows = connection.execute(
            """WITH RECURSIVE ancestors(id) AS (
                   SELECT prerequisite_topic_id FROM learning_topic_prerequisites WHERE topic_id=?
                   UNION
                   SELECT p.prerequisite_topic_id
                   FROM learning_topic_prerequisites p JOIN ancestors a ON p.topic_id=a.id
               )
               SELECT t.id, t.topic, t.status
               FROM ancestors a JOIN learning_path_topics t ON t.id=a.id
               WHERE t.status!='completed' ORDER BY t.position, t.id""",
            (topic_id,),
        ).fetchall()
    return [dict(row) for row in rows]
