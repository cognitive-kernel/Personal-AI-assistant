"""Evaluator-authorized transitions for sequential learning methods."""

from __future__ import annotations

import json
from typing import Any

from .db import learning_connection
from .learning_topic_method_attempts import MethodAttemptError, append_attempt_event
from .learning_method_evaluation_contracts import _assert_persisted_evaluation
from .learning_method_state import (
    _assert_task_context,
    _content_hash,
    _method_attempt_identity,
    TOPIC_QUALITY_TASK_TYPE,
)





def complete_method(
    job_id: int,
    *,
    score: float,
    evaluation_id: int,
    content_sha256: str,
    diagnosis: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    task_context: Any | None = None,
) -> dict[str, Any]:
    """Atomically fence the pass event and gate transition before queuing the next topic."""
    from . import learning

    job = learning.get_job(job_id)
    identity = _method_attempt_identity(job)
    if identity is None:
        return {"status": "not_method_scoped", "job_id": job_id}
    path_topic_id, method, attempt_number = identity
    already_passed = False
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_task_context(
            connection, task_context, task_type=TOPIC_QUALITY_TASK_TYPE,
            payload_fields={
                "job_id": job_id,
                "path_topic_id": path_topic_id,
                "content_sha256": content_sha256,
            },
        )
        _assert_persisted_evaluation(
            connection,
            evaluation_id=evaluation_id,
            path_topic_id=path_topic_id,
            job_id=job_id,
            content_sha256=content_sha256,
            outcome="passed",
            score=score,
        )
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
        if gate is None or gate["current_job_id"] is None or int(gate["current_job_id"]) != job_id:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate["state"] == "blocked":
            return {"status": "blocked", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate["state"] == "passed":
            already_passed = True
        else:
            append_attempt_event(
                path_topic_id, method, attempt_number, "passed",
                event_key=f"{job['method_attempt_key']}:passed",
                job_id=job_id,
                content_sha256=content_sha256,
                score=score,
                diagnosis=diagnosis,
                evidence={"evaluation_id": evaluation_id, **(evidence or {})},
                _connection=connection,
            )
            updated = connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='passed', latest_score=?, latest_content_sha256=?,
                       latest_evaluation_id=?, diagnosis_json=?, next_action='queue_next_topic',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=? AND current_job_id=? AND state NOT IN ('passed','blocked')""",
                (
                    score, content_sha256, evaluation_id,
                    json.dumps(diagnosis, ensure_ascii=False, sort_keys=True),
                    path_topic_id, job_id,
                ),
            )
            if updated.rowcount != 1:
                return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
            connection.execute(
                """UPDATE learning_path_topics
                   SET status='completed', updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (path_topic_id,),
            )
    next_topic = learning.queue_next_researched_path_topic(path_topic_id)
    return {
        "status": "already_passed" if already_passed else "passed",
        "path_topic_id": path_topic_id, "job_id": job_id,
        "method": method, "score": score, "evaluation_id": evaluation_id,
        "next_topic": next_topic,
    }

def block_unavailable(
    job_id: int,
    *,
    reason: str,
    evaluation_id: int | None = None,
    diagnosis: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    task_context: Any | None = None,
) -> dict[str, Any]:
    """Atomically record evaluator unavailability for the current task owner."""
    from . import learning

    job = learning.get_job(job_id)
    identity = _method_attempt_identity(job)
    if identity is None:
        return {"status": "not_method_scoped", "job_id": job_id}
    path_topic_id, method, attempt_number = identity
    details = {"reason": reason, **(diagnosis or {})}
    event_evidence = {"evaluation_id": evaluation_id, **(evidence or {})}
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_task_context(
            connection, task_context, task_type=TOPIC_QUALITY_TASK_TYPE,
            payload_fields={
                "job_id": job_id,
                "path_topic_id": path_topic_id,
                "content_sha256": _content_hash(job),
            },
        )
        if isinstance(evaluation_id, bool) or not isinstance(evaluation_id, int) or evaluation_id < 1:
            raise MethodAttemptError("A persisted unavailable evaluation ID is required.")
        _assert_persisted_evaluation(
            connection,
            evaluation_id=evaluation_id,
            path_topic_id=path_topic_id,
            job_id=job_id,
            content_sha256=str(_content_hash(job) or ""),
            outcome="unavailable",
            score=None,
        )
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
        if gate is None or gate["current_job_id"] is None or int(gate["current_job_id"]) != job_id:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate["state"] == "passed":
            return {"status": "passed", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate["state"] == "blocked":
            return {"status": "blocked", "path_topic_id": path_topic_id, "job_id": job_id}
        append_attempt_event(
            path_topic_id, method, attempt_number, "unavailable",
            event_key=f"{job['method_attempt_key']}:unavailable",
            job_id=job_id,
            content_sha256=_content_hash(job),
            diagnosis=details,
            evidence=event_evidence,
            _connection=connection,
        )
        updated = connection.execute(
            """UPDATE learning_topic_quality_gates
               SET state='blocked', latest_evaluation_id=?, diagnosis_json=?,
                   next_action='configure_or_restore_independent_evaluator',
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND current_job_id=? AND state NOT IN ('passed','blocked')""",
            (
                evaluation_id, json.dumps(details, ensure_ascii=False, sort_keys=True),
                path_topic_id, job_id,
            ),
        )
        if updated.rowcount != 1:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
        connection.execute(
            """UPDATE learning_path_topics SET status='needs_review', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status!='completed'""",
            (path_topic_id,),
        )
    return {"status": "blocked", "path_topic_id": path_topic_id, "reason": reason}
