"""Fenced state transitions for learning-method execution and evaluation."""

from __future__ import annotations

import json
from typing import Any

from .db import learning_connection
from .learning_topic_method_attempts import (
    MethodAttemptError,
    append_attempt_event,
    latest_attempt_event,
)
from .learning_method_state import (
    _assert_task_context,
    _block_topic,
    _content_hash,
    _method_attempt_identity,
    LEARNING_JOB_TASK_TYPE,
    TOPIC_QUALITY_TASK_TYPE,
)
from .learning_method_queue import _queue_next


def mark_method_running(
    job_id: int, *, execution_lease=None, task_context: Any | None = None
) -> None:
    """Append the running event only while this domain execution epoch is current."""
    from . import learning, learning_execution

    job = learning.get_job(job_id)
    identity = _method_attempt_identity(job)
    if identity is None:
        return
    path_topic_id, method, attempt_number = identity
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if execution_lease is not None:
            learning_execution.assert_current(execution_lease, connection=connection)
        if task_context is not None:
            _assert_task_context(
                connection, task_context, task_type=LEARNING_JOB_TASK_TYPE,
                payload_fields={"job_id": job_id},
            )
        latest = connection.execute(
            """SELECT status FROM learning_topic_method_attempt_events
               WHERE path_topic_id=? AND method=? AND attempt_number=?
               ORDER BY event_sequence DESC LIMIT 1""",
            (path_topic_id, method, attempt_number),
        ).fetchone()
        if latest is not None and latest["status"] == "running":
            return
        if latest is None:
            append_attempt_event(
                path_topic_id, method, attempt_number, "queued",
                event_key=f"{job['method_attempt_key']}:queued",
                job_id=job_id,
                evidence={"recovered_missing_queue_event": True},
                _connection=connection,
            )
        append_attempt_event(
            path_topic_id, method, attempt_number, "running",
            event_key=f"{job['method_attempt_key']}:running",
            job_id=job_id,
            evidence={"worker_claimed_job": True},
            _connection=connection,
        )

def mark_method_cancelled(job_id: int, *, reason: str = "Cancelled by user.") -> None:
    """Record cancellation only after the durable learning job is actually cancelled."""
    from . import learning

    job = learning.get_job(job_id)
    if job.get("status") != "cancelled":
        return
    identity = _method_attempt_identity(job)
    if identity is None:
        return
    path_topic_id, method, attempt_number = identity
    latest = latest_attempt_event(path_topic_id, method, attempt_number)
    if latest is not None and latest["status"] == "cancelled":
        return
    append_attempt_event(
        path_topic_id, method, attempt_number, "cancelled",
        event_key=f"{job['method_attempt_key']}:cancelled",
        job_id=job_id,
        content_sha256=_content_hash(job),
        diagnosis={"reason": str(reason)[:500], "terminal_state": "cancelled"},
        evidence={"cancelled_by_user": True},
    )

def advance_after_failure(
    job_id: int,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
    score: float | None = None,
    diagnosis: dict[str, Any] | None = None,
    outcome: str = "failed",
    task_context: Any | None = None,
    expected_task_type: str = TOPIC_QUALITY_TASK_TYPE,
) -> dict[str, Any]:
    """Commit a failure event only for the current task owner, then advance once."""
    from . import learning

    job = learning.get_job(job_id)
    identity = _method_attempt_identity(job)
    if identity is None:
        return {"status": "not_method_scoped", "job_id": job_id}
    path_topic_id, method, attempt_number = identity
    if outcome not in {"failed", "inconclusive"}:
        raise MethodAttemptError("Failure outcome must be failed or inconclusive.")
    stable_reason = str(job.get("error") or reason)[:1000] if outcome == "failed" else str(reason)[:1000]
    details = dict(diagnosis or {})
    details.setdefault("reason", stable_reason)
    event_evidence = {"failure_stage": "generation"} if details.get("failure_stage") == "generation" else (evidence or {})
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_task_context(
            connection, task_context, task_type=expected_task_type,
            payload_fields={"job_id": job_id},
        )
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
        if gate is None or gate["current_job_id"] is None or int(gate["current_job_id"]) != job_id:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate["state"] in {"passed", "blocked"}:
            return {"status": str(gate["state"]), "path_topic_id": path_topic_id, "job_id": job_id}
        append_attempt_event(
            path_topic_id, method, attempt_number, outcome,
            event_key=f"{job['method_attempt_key']}:{outcome}",
            job_id=job_id,
            content_sha256=_content_hash(job),
            score=score,
            diagnosis=details,
            evidence=event_evidence,
            _connection=connection,
        )
        updated = connection.execute(
            """UPDATE learning_topic_quality_gates
               SET state='pending', diagnosis_json=?, next_action='advance_method',
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND current_job_id=? AND state NOT IN ('passed','blocked')""",
            (
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                path_topic_id, job_id,
            ),
        )
        if updated.rowcount != 1:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
    return _queue_next(path_topic_id, method, attempt_number, source_job_id=job_id)

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
            payload_fields={"job_id": job_id},
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
            payload_fields={"job_id": job_id},
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

def block_without_quality_gate(
    path_topic_id: int, *, job_id: int, task_context: Any | None = None
) -> dict[str, Any]:
    """Fail closed when a completed curriculum job has no independent quality gate."""
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_task_context(
            connection, task_context, task_type=LEARNING_JOB_TASK_TYPE,
            payload_fields={"job_id": job_id},
        )
        gate = connection.execute(
            "SELECT current_job_id, state FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
        if gate is not None and gate["current_job_id"] is not None and int(gate["current_job_id"]) != job_id:
            return {"status": "stale_attempt", "path_topic_id": path_topic_id, "job_id": job_id}
        if gate is not None and gate["state"] == "passed":
            return {"status": "passed", "path_topic_id": path_topic_id, "job_id": job_id}
        connection.execute(
            """INSERT OR IGNORE INTO learning_topic_quality_gates(
                   path_topic_id, current_job_id, state, next_action, diagnosis_json
               ) VALUES (?, ?, 'blocked', 'manual_review_required', ?)""",
            (
                path_topic_id, job_id,
                json.dumps({"reason": "missing_quality_gate"}, sort_keys=True),
            ),
        )
        connection.execute(
            """UPDATE learning_topic_quality_gates
               SET current_job_id=?, state='blocked', next_action='manual_review_required',
                   diagnosis_json=?, updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND (current_job_id IS NULL OR current_job_id=?)""",
            (
                job_id,
                json.dumps({"reason": "missing_quality_gate"}, sort_keys=True),
                path_topic_id, job_id,
            ),
        )
        connection.execute(
            """UPDATE learning_path_topics SET status='needs_review', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status!='completed'""",
            (path_topic_id,),
        )
    return {"status": "blocked", "path_topic_id": path_topic_id, "reason": "missing_quality_gate"}

def record_generation_failure(
    job_id: int,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
    task_context: Any | None = None,
) -> dict[str, Any]:
    """Record a terminal provider/job failure and advance under the current queue lease."""
    return advance_after_failure(
        job_id,
        reason=reason,
        evidence={"failure_stage": "generation"},
        diagnosis={"failure_stage": "generation"},
        task_context=task_context,
        expected_task_type=LEARNING_JOB_TASK_TYPE,
    )
