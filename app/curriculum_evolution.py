"""Auditable, evidence-triggered proposals for adapting saved learning curricula.

Weak assessment scores can propose a reinforcement topic. A changed source can
propose lesson refresh. Nothing mutates a path or queues regeneration until a
user explicitly accepts a proposal.
"""
from __future__ import annotations

import json
from typing import Any

from .db import init_databases, learning_connection

_PROPOSAL_TYPES = {"reinforce_topic", "refresh_lesson"}
_REVIEW_DECISIONS = {"accept", "reject"}


def _decode_proposal(row) -> dict[str, Any]:
    result = dict(row)
    try:
        result["evidence"] = json.loads(result.pop("evidence_json"))
    except (TypeError, json.JSONDecodeError):
        result["evidence"] = {}
    return result


def list_proposals(*, path_id: int, limit: int = 50) -> dict[str, Any]:
    if isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1:
        raise ValueError("path_id must be a positive integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100.")
    init_databases()
    with learning_connection() as connection:
        if connection.execute("SELECT 1 FROM learning_paths WHERE id=?", (path_id,)).fetchone() is None:
            raise KeyError(path_id)
        rows = connection.execute(
            """SELECT id, path_id, trigger_topic_id, source_sync_event_id, proposal_type,
                      proposed_topic, rationale, evidence_json, status, review_note,
                      reviewed_at, applied_topic_id, created_at
               FROM curriculum_update_proposals
               WHERE path_id=? ORDER BY id DESC LIMIT ?""",
            (path_id, limit),
        ).fetchall()
    proposals = [_decode_proposal(row) for row in rows]
    return {"path_id": path_id, "count": len(proposals), "proposals": proposals}


def generate_proposals(*, path_id: int) -> dict[str, Any]:
    """Create bounded proposals from persisted learning outcomes and source-change evidence."""
    if isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1:
        raise ValueError("path_id must be a positive integer.")
    init_databases()
    created = 0
    with learning_connection() as connection:
        path = connection.execute(
            "SELECT id FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone()
        if path is None:
            raise KeyError(path_id)
        topics = connection.execute(
            """SELECT id, topic, position, status, latest_score, updated_at
               FROM learning_path_topics WHERE path_id=? ORDER BY position, id""",
            (path_id,),
        ).fetchall()
        for topic in topics:
            score = topic["latest_score"]
            if topic["status"] != "completed" or score is None:
                continue
            try:
                numeric_score = float(score)
            except (TypeError, ValueError):
                continue
            if not 0 <= numeric_score < 80:
                continue
            proposed_topic = "مرور تقویتی: " + str(topic["topic"]).strip()
            if len(proposed_topic) > 300:
                proposed_topic = proposed_topic[:300]
            if connection.execute(
                "SELECT 1 FROM learning_path_topics WHERE path_id=? AND topic=?",
                (path_id, proposed_topic),
            ).fetchone():
                continue
            if connection.execute(
                """SELECT 1 FROM curriculum_update_proposals
                   WHERE path_id=? AND trigger_topic_id=? AND proposal_type='reinforce_topic'""",
                (path_id, topic["id"]),
            ).fetchone():
                continue
            evidence = {
                "trigger": "persisted_assessment_score",
                "latest_score": numeric_score,
                "score_semantics": "normalized_exact_match_not_semantic_mastery",
                "topic_position": int(topic["position"]),
                "topic_updated_at": topic["updated_at"],
            }
            connection.execute(
                """INSERT INTO curriculum_update_proposals(
                       path_id, trigger_topic_id, proposal_type, proposed_topic,
                       rationale, evidence_json
                   ) VALUES (?, ?, 'reinforce_topic', ?, ?, ?)""",
                (
                    path_id,
                    topic["id"],
                    proposed_topic,
                    f"Topic score {numeric_score:g}/100 is below the 80-point reinforcement threshold; a review topic is proposed before proceeding further.",
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                ),
            )
            created += 1

        source_events = connection.execute(
            """SELECT t.id AS topic_id, t.topic, e.id AS event_id, e.observed_sha256,
                      s.source_ref, s.snapshot_sha256, e.observed_text
               FROM learning_source_sync_events AS e
               JOIN learning_source_snapshots AS s ON s.id=e.snapshot_id
               JOIN learning_jobs AS j ON j.id=s.job_id
               JOIN learning_path_topics AS t ON t.id=j.path_topic_id
               WHERE t.path_id=? AND e.outcome='changed'
                 AND e.review_decision='pending' AND length(trim(COALESCE(e.observed_text, ''))) > 0
               ORDER BY e.id LIMIT 20""",
            (path_id,),
        ).fetchall()
        for event in source_events:
            exists = connection.execute(
                "SELECT 1 FROM curriculum_update_proposals WHERE source_sync_event_id=?",
                (event["event_id"],),
            ).fetchone()
            if exists:
                continue
            evidence = {
                "trigger": "live_source_snapshot_changed",
                "source_sync_event_id": int(event["event_id"]),
                "source_url": event["source_ref"],
                "previous_snapshot_sha256": event["snapshot_sha256"],
                "observed_sha256": event["observed_sha256"],
                "resolution_required": "human review before lesson regeneration",
            }
            connection.execute(
                """INSERT INTO curriculum_update_proposals(
                       path_id, trigger_topic_id, source_sync_event_id, proposal_type,
                       proposed_topic, rationale, evidence_json
                   ) VALUES (?, ?, ?, 'refresh_lesson', ?, ?, ?)""",
                (
                    path_id,
                    event["topic_id"],
                    event["event_id"],
                    str(event["topic"])[:300],
                    "A live source excerpt differs from the version used for this lesson; review the source change and decide whether to regenerate the lesson.",
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                ),
            )
            created += 1

    result = list_proposals(path_id=path_id)
    result["created"] = created
    result["autonomous_mutation"] = False
    return result


def _insert_reinforcement_topic(connection, proposal: dict[str, Any]) -> int:
    path_id = int(proposal["path_id"])
    trigger_id = int(proposal["trigger_topic_id"])
    rows = [
        dict(row) for row in connection.execute(
            """SELECT id, topic, position FROM learning_path_topics
               WHERE path_id=? ORDER BY position, id""",
            (path_id,),
        ).fetchall()
    ]
    trigger_index = next((index for index, row in enumerate(rows) if int(row["id"]) == trigger_id), None)
    if trigger_index is None:
        raise ValueError("The proposal's trigger topic no longer belongs to the path.")
    existing = connection.execute(
        "SELECT id FROM learning_path_topics WHERE path_id=? AND topic=?",
        (path_id, proposal["proposed_topic"]),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])

    offset = max((int(row["position"]) for row in rows), default=0) + len(rows) * 4 + 100
    for index, row in enumerate(rows):
        connection.execute(
            "UPDATE learning_path_topics SET position=? WHERE id=?",
            (offset + index * 2, row["id"]),
        )
    new_position = offset + trigger_index * 2 + 1
    cursor = connection.execute(
        """INSERT INTO learning_path_topics(path_id, topic, position, status)
           VALUES (?, ?, ?, 'pending')""",
        (path_id, proposal["proposed_topic"], new_position),
    )
    topic_id = int(cursor.lastrowid)
    ordered = connection.execute(
        "SELECT id FROM learning_path_topics WHERE path_id=? ORDER BY position, id",
        (path_id,),
    ).fetchall()
    for index, row in enumerate(ordered, start=1):
        connection.execute(
            "UPDATE learning_path_topics SET position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (index, row["id"]),
        )
    return topic_id


def review_proposal(
    proposal_id: int,
    *,
    decision: str,
    note: str,
) -> dict[str, Any]:
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ValueError("proposal_id must be a positive integer.")
    if not isinstance(decision, str) or decision not in _REVIEW_DECISIONS:
        raise ValueError("decision must be accept or reject.")
    if not isinstance(note, str) or not 20 <= len(note.strip()) <= 1000:
        raise ValueError("note must contain 20–1000 non-whitespace characters.")
    init_databases()
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT id, path_id, trigger_topic_id, source_sync_event_id, proposal_type,
                      proposed_topic, rationale, evidence_json, status
               FROM curriculum_update_proposals WHERE id=?""",
            (proposal_id,),
        ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    proposal = dict(row)
    if proposal["status"] != "pending":
        raise ValueError("A reviewed curriculum proposal is immutable.")

    regeneration = None
    if proposal["proposal_type"] == "refresh_lesson":
        from . import learning_source_sync

        event_id = int(proposal["source_sync_event_id"])
        with learning_connection() as connection:
            event = connection.execute(
                "SELECT review_decision FROM learning_source_sync_events WHERE id=?",
                (event_id,),
            ).fetchone()
        if event is None:
            raise ValueError("The source-change event no longer exists.")
        if decision == "accept":
            if event["review_decision"] == "pending":
                learning_source_sync.record_review_decision(
                    event_id, decision="regenerate_lesson", note=note
                )
            elif event["review_decision"] != "regenerate_lesson":
                raise ValueError("The source-change event already has a conflicting decision.")
            regeneration = learning_source_sync.queue_lesson_regeneration(event_id)
            new_status = "accepted"
        else:
            if event["review_decision"] == "pending":
                learning_source_sync.record_review_decision(
                    event_id, decision="keep_lesson", note=note
                )
            elif event["review_decision"] != "keep_lesson":
                raise ValueError("The source-change event already has a conflicting decision.")
            new_status = "rejected"
        with learning_connection() as connection:
            connection.execute(
                """UPDATE curriculum_update_proposals
                   SET status=?, review_note=?, reviewed_at=CURRENT_TIMESTAMP,
                       applied_topic_id=?
                   WHERE id=? AND status='pending'""",
                (new_status, note.strip(), proposal["trigger_topic_id"], proposal_id),
            )
    else:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM curriculum_update_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
            if current is None:
                raise KeyError(proposal_id)
            if current["status"] != "pending":
                raise ValueError("A reviewed curriculum proposal is immutable.")
            if decision == "accept":
                applied_topic_id = _insert_reinforcement_topic(connection, proposal)
                new_status = "accepted"
            else:
                applied_topic_id = None
                new_status = "rejected"
            connection.execute(
                """UPDATE curriculum_update_proposals
                   SET status=?, review_note=?, reviewed_at=CURRENT_TIMESTAMP,
                       applied_topic_id=?
                   WHERE id=? AND status='pending'""",
                (new_status, note.strip(), applied_topic_id, proposal_id),
            )

    with learning_connection() as connection:
        saved = connection.execute(
            """SELECT id, path_id, trigger_topic_id, source_sync_event_id, proposal_type,
                      proposed_topic, rationale, evidence_json, status, review_note,
                      reviewed_at, applied_topic_id, created_at
               FROM curriculum_update_proposals WHERE id=?""",
            (proposal_id,),
        ).fetchone()
    result = {"proposal": _decode_proposal(saved)}
    if regeneration is not None:
        result["regeneration"] = regeneration
    return result
