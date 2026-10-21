"""Read-only aggregate metrics for the self-correcting learning pipeline.

These are audit/event counts, not probabilities of truth or claims of model quality.
"""
from __future__ import annotations

from typing import Any

from . import db


_FAILURE_TYPES = ("consensus_failure", "test_failure", "usage_failure")


def get_learning_metrics() -> dict[str, Any]:
    """Return bounded, database-derived trust and failure-resolution metrics."""
    db.init_databases()
    with db.learning_connection() as conn:
        candidate_rows = conn.execute(
            """SELECT status, trust_level, learning_status, COUNT(*) AS count
               FROM temporary_knowledge_candidates
               GROUP BY status, trust_level, learning_status
               ORDER BY trust_level, learning_status, status"""
        ).fetchall()
        knowledge_rows = conn.execute(
            """SELECT status, trust_level, learning_status, COUNT(*) AS count
               FROM knowledge_items
               GROUP BY status, trust_level, learning_status
               ORDER BY trust_level, learning_status, status"""
        ).fetchall()
        experience_rows = conn.execute(
            """SELECT experience_type, status, COUNT(*) AS count
               FROM learning_experiences
               GROUP BY experience_type, status
               ORDER BY experience_type, status"""
        ).fetchall()
        correction_rows = conn.execute(
            """SELECT status, COUNT(*) AS count
               FROM knowledge_self_correction_attempts
               GROUP BY status ORDER BY status"""
        ).fetchall()
        monthly_rows = conn.execute(
            """SELECT substr(created_at, 1, 7) AS month,
                      COUNT(*) AS experience_events,
                      SUM(CASE WHEN experience_type IN
                          ('consensus_failure','test_failure','usage_failure')
                          THEN 1 ELSE 0 END) AS failure_events,
                      SUM(CASE WHEN experience_type='successful_alternative'
                          THEN 1 ELSE 0 END) AS successful_alternatives
               FROM learning_experiences
               WHERE datetime(created_at) >= datetime('now', '-365 days')
               GROUP BY substr(created_at, 1, 7)
               ORDER BY month ASC"""
        ).fetchall()
        active_total = int(conn.execute(
            """SELECT COUNT(*) FROM knowledge_items
               WHERE status!='superseded' AND learning_status!='superseded'"""
        ).fetchone()[0])
        trusted_active = int(conn.execute(
            """SELECT COUNT(*) FROM knowledge_items
               WHERE status!='superseded' AND learning_status!='superseded'
                 AND trust_level IN ('L2','L3')"""
        ).fetchone()[0])
        failure_total = int(conn.execute(
            """SELECT COUNT(*) FROM learning_experiences
               WHERE experience_type IN ('consensus_failure','test_failure','usage_failure')"""
        ).fetchone()[0])
        alternative_total = int(conn.execute(
            "SELECT COUNT(*) FROM learning_experiences WHERE experience_type='successful_alternative'"
        ).fetchone()[0])
        resolved_hours = conn.execute(
            """SELECT AVG((julianday(k.created_at)-julianday(a.created_at))*24.0)
               FROM knowledge_self_correction_attempts a
               JOIN temporary_knowledge_candidates c ON c.id=a.alternative_candidate_id
               JOIN knowledge_items k ON k.id=c.promoted_knowledge_id
               WHERE a.status='resolved' AND k.learning_status IN ('tested','human_verified')"""
        ).fetchone()[0]

    def as_counts(rows: list[Any], fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {**{field: str(row[field]) for field in fields}, "count": int(row["count"])}
            for row in rows
        ]

    return {
        "candidate_states": as_counts(candidate_rows, ("status", "trust_level", "learning_status")),
        "durable_knowledge_states": as_counts(knowledge_rows, ("status", "trust_level", "learning_status")),
        "experience_states": as_counts(experience_rows, ("experience_type", "status")),
        "self_correction_attempts": as_counts(correction_rows, ("status",)),
        "active_knowledge": {
            "total": active_total,
            "tested_or_human_verified": trusted_active,
            "tested_share": round(trusted_active / active_total, 4) if active_total else None,
        },
        "failure_events_total": failure_total,
        "successful_alternatives_total": alternative_total,
        "average_hours_to_tested_alternative": (
            round(max(0.0, float(resolved_hours)), 2) if resolved_hours is not None else None
        ),
        "monthly_experience_events_last_365_days": [
            {
                "month": str(row["month"]),
                "experience_events": int(row["experience_events"]),
                "failure_events": int(row["failure_events"] or 0),
                "successful_alternatives": int(row["successful_alternatives"] or 0),
            }
            for row in monthly_rows
        ],
        "methodology": {
            "failure_events_are_experience_records_not_unique_failed_knowledge_items": True,
            "monthly_values_are_counts_not_failure_rates": True,
            "model_consensus_is_not_truth": True,
            "tested_share_excludes_superseded_knowledge": True,
            "average_resolution_hours_requires_a_resolved_attempt_and_tested_alternative": True,
        },
    }
