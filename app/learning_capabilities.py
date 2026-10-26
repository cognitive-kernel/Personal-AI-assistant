"""Read-only capability map derived from persisted learning assessments.

The map reports evidence strength, not a claim of semantic mastery. Exact-match
assessment scores remain provisional until repeatable practical tasks are added.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .db import init_databases, learning_connection

_MAX_LIMIT = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _practical_evidence_map(connection, topics: set[str]) -> dict[str, dict[str, Any]]:
    """Count distinct successful sandbox exercises with live source provenance."""
    result_map: dict[str, dict[str, Any]] = {}
    if not topics:
        return result_map
    rows = connection.execute(
        """SELECT topic, test_results_json FROM learning_experiences
           WHERE status='verified' ORDER BY id DESC LIMIT 5000"""
    ).fetchall()
    valid_by_topic: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for row in rows:
        topic = str(row["topic"]).strip().casefold()
        if topic not in topics:
            continue
        try:
            results = json.loads(row["test_results_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict) or item.get("passed") is not True or item.get("runner") != "docker-sandbox":
                continue
            test_name, source_hash = item.get("test"), item.get("source_sha256")
            if not isinstance(test_name, str) or not test_name.strip() or not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
                continue
            sources = item.get("source_evidence")
            if not isinstance(sources, list) or not sources:
                continue
            if not all(
                isinstance(source, dict)
                and isinstance(source.get("url"), str)
                and isinstance(source.get("live_content_sha256"), str)
                and _SHA256.fullmatch(source["live_content_sha256"])
                and isinstance(source.get("excerpt_sha256"), str)
                and _SHA256.fullmatch(source["excerpt_sha256"])
                for source in sources
            ):
                continue
            valid_by_topic.setdefault(topic, {})[(test_name.strip(), source_hash)] = item
    for topic in topics:
        valid = valid_by_topic.get(topic.casefold(), {})
        hashes = {source_hash for _, source_hash in valid}
        names = {name for name, _ in valid}
        result_map[topic] = {
            "practical_attempt_count": len(valid),
            "practical_test_names": sorted(names)[:20],
            "practical_source_count": len(hashes),
            "practical_repeatable": len(hashes) >= 2 and len(names) >= 2,
        }
    return result_map



def get_capability_map(*, path_id: int | None = None, limit: int = 200) -> dict[str, Any]:
    if path_id is not None and (
        isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1
    ):
        raise ValueError("path_id must be a positive integer when provided.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {_MAX_LIMIT}.")
    init_databases()
    with learning_connection() as connection:
        if path_id is not None and connection.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone() is None:
            raise KeyError(path_id)
        sql = """SELECT p.id AS path_id, p.title AS path_title, p.curriculum_slug,
                        t.id AS topic_id, t.topic, t.position, t.status AS topic_status,
                        t.latest_score AS persisted_topic_score, t.updated_at AS topic_updated_at,
                        (SELECT COUNT(*)
                         FROM assessment_attempts a
                         JOIN learning_jobs j ON j.id=a.job_id
                         WHERE j.path_topic_id=t.id
                           AND a.scoring_method='normalized_exact_match'
                           AND a.score BETWEEN 0 AND 100
                           AND a.question_count > 0
                           AND a.correct_count BETWEEN 0 AND a.question_count) AS assessment_count,
                        latest.id AS latest_attempt_id,
                        latest.score AS latest_attempt_score,
                        latest.correct_count AS latest_correct_count,
                        latest.question_count AS latest_question_count,
                        latest.scoring_method AS latest_scoring_method,
                        latest.created_at AS latest_assessed_at
                 FROM learning_path_topics t
                 JOIN learning_paths p ON p.id=t.path_id
                 LEFT JOIN assessment_attempts latest ON latest.id=(
                    SELECT a.id
                    FROM assessment_attempts a
                    JOIN learning_jobs j ON j.id=a.job_id
                    WHERE j.path_topic_id=t.id
                    ORDER BY a.id DESC LIMIT 1
                 )"""
        params: list[Any] = []
        if path_id is not None:
            sql += " WHERE p.id=?"
            params.append(path_id)
        sql += " ORDER BY p.id, t.position, t.id LIMIT ?"
        params.append(limit)
        rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
        practical_by_topic = _practical_evidence_map(connection, {str(row["topic"]).strip().casefold() for row in rows})

    capabilities: list[dict[str, Any]] = []
    for row in rows:
        score = row["latest_attempt_score"]
        correct = row["latest_correct_count"]
        questions = row["latest_question_count"]
        method = row["latest_scoring_method"]
        count = int(row["assessment_count"] or 0)
        valid = (
            row["latest_attempt_id"] is not None
            and method == "normalized_exact_match"
            and isinstance(score, (int, float))
            and 0 <= float(score) <= 100
            and isinstance(correct, int)
            and isinstance(questions, int)
            and questions > 0
            and 0 <= correct <= questions
        )
        if row["latest_attempt_id"] is None:
            status = "not_assessed"
            confidence = "none"
        elif not valid:
            status = "invalid_evidence"
            confidence = "none"
            score = None
        elif float(score) < 80:
            status = "needs_reinforcement"
            confidence = "provisional"
        elif count >= 2:
            status = "repeated_provisional"
            confidence = "provisional_repeat"
        else:
            status = "provisional"
            confidence = "provisional"

        if practical["practical_repeatable"]:
            if valid and float(score) < 80:
                status = "conflicting_evidence"
                confidence = "conflicting"
            else:
                status = "practical_provisional"
                confidence = "provisional_practical"

        capabilities.append({
            "path_id": int(row["path_id"]),
            "path_title": row["path_title"],
            "curriculum_slug": row["curriculum_slug"],
            "topic_id": int(row["topic_id"]),
            "capability": row["topic"],
            "topic_position": int(row["position"]),
            "topic_status": row["topic_status"],
            "status": status,
            "latest_score": float(score) if valid else None,
            "assessment_count": count,
            **practical,
            "latest_attempt_id": row["latest_attempt_id"],
            "correct_count": int(correct) if valid else None,
            "question_count": int(questions) if valid else None,
            "scoring_method": method if valid else None,
            "last_assessed_at": row["latest_assessed_at"] if valid else None,
            "confidence": confidence,
            "evidence_limit": (
                "Repeated practical evidence means two distinct passing sandbox test programs with current-page source excerpts; it remains provisional and does not prove general transfer or semantic mastery."
                if practical["practical_repeatable"] else
                "Exact-match assessments are provisional evidence; semantic mastery and practical transfer are not established."
            ),
        })

    return {
        "count": len(capabilities),
        "path_id": path_id,
        "capabilities": capabilities,
        "summary": {
            "not_assessed": sum(item["status"] == "not_assessed" for item in capabilities),
            "invalid_evidence": sum(item["status"] == "invalid_evidence" for item in capabilities),
            "needs_reinforcement": sum(item["status"] == "needs_reinforcement" for item in capabilities),
            "provisional": sum(item["status"] == "provisional" for item in capabilities),
            "repeated_provisional": sum(item["status"] == "repeated_provisional" for item in capabilities),
            "practical_provisional": sum(item["status"] == "practical_provisional" for item in capabilities),
            "conflicting_evidence": sum(item["status"] == "conflicting_evidence" for item in capabilities),
        },
    }
