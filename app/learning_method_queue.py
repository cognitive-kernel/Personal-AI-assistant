"""Idempotent method selection, job creation, and fallback queueing."""

from __future__ import annotations

import json
from typing import Any

from .db import learning_connection
from .learning_topic_method_attempts import (
    METHOD_ORDER,
    MethodAttemptError,
    append_attempt_event,
    latest_attempt_event,
    list_attempt_events,
    next_method,
)
from .learning_method_state import (
    _block_topic,
    _content_hash,
    _find_job_by_method_key,
    _method_attempt_identity,
    _method_enabled,
    _topic_context,
)


def queue_method_attempt(
    path_topic_id: int,
    method: str,
    *,
    attempt_number: int = 1,
    source_job_id: int | None = None,
    lesson_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or recover exactly one method job, then publish it to the durable queue."""
    from . import learning

    if method not in METHOD_ORDER:
        raise MethodAttemptError("Unsupported learning method.")
    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or not 1 <= attempt_number <= 3:
        raise MethodAttemptError("attempt_number must be between 1 and 3.")
    topic, _path_id = _topic_context(path_topic_id)
    method_key = f"path-topic:{path_topic_id}:method:{method}:attempt:{attempt_number}"
    event_key = method_key + ":queued"
    latest = latest_attempt_event(path_topic_id, method, attempt_number)

    if latest is None:
        append_attempt_event(
            path_topic_id, method, attempt_number, "queued",
            event_key=event_key,
            evidence={"method_order": list(METHOD_ORDER), "method_key": method_key},
        )
    elif latest["status"] in {"failed", "unavailable", "inconclusive", "passed", "cancelled"}:
        raise MethodAttemptError("This method attempt is already terminal.")

    job = _find_job_by_method_key(method_key)
    if job is None:
        source_text = ""
        if source_job_id is not None:
            with learning_connection() as connection:
                source = connection.execute(
                    "SELECT content FROM learning_source_text WHERE job_id=?",
                    (source_job_id,),
                ).fetchone()
            source_text = str(source["content"]) if source is not None else ""
        try:
            job = learning.create_job(
                topic,
                source_text=source_text or None,
                path_topic_id=path_topic_id,
                learning_method=method,
                method_attempt_key=method_key,
                lesson_review=lesson_review,
            )
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            append_attempt_event(
                path_topic_id, method, attempt_number, "failed",
                event_key=method_key + ":job_creation_failed",
                diagnosis={
                    "reason": "Learning method job creation rejected its input or contract.",
                    "failure_stage": "job_creation",
                    "exception_type": type(exc).__name__,
                },
                evidence={
                    "failure_stage": "job_creation",
                    "source_job_id": source_job_id,
                },
            )
            return _queue_next(
                path_topic_id, method, attempt_number, source_job_id=source_job_id
            )
    job_id = int(job["id"])

    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT OR IGNORE INTO learning_topic_quality_gates(
                   path_topic_id, state, next_action
               ) VALUES (?, 'pending', ?)""",
            (path_topic_id, f"run_{method}"),
        )
        connection.execute(
            """UPDATE learning_topic_quality_gates
               SET current_job_id=?, state='pending', next_action=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND state!='passed'""",
            (job_id, f"run_{method}", path_topic_id),
        )
        connection.execute(
            """UPDATE learning_path_topics
               SET status='in_progress', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status!='completed'""",
            (path_topic_id,),
        )

    if job.get("status") == "queued":
        task = learning._enqueue_learning_job(job_id)
    else:
        task = None
    return {
        "status": "queued" if task is not None else str(job.get("status", "unknown")),
        "path_topic_id": path_topic_id,
        "method": method,
        "attempt_number": attempt_number,
        "job": job,
        "task": task,
        "method_attempt_key": method_key,
    }

def queue_initial_method(
    path_topic_id: int,
    *,
    force_new_cycle: bool = False,
    lesson_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume the current method or start AI Learning as the first method."""
    from . import learning
    from .learning_topic_quality_tasks import enqueue_topic_quality_evaluation
    from .learning_method_transitions import complete_method, record_generation_failure

    _topic, _path_id = _topic_context(path_topic_id)
    with learning_connection() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO learning_topic_quality_gates(
                   path_topic_id, state, next_action
               ) VALUES (?, 'pending', 'start_ai_learning')""",
            (path_topic_id,),
        )
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
    if force_new_cycle:
        with learning_connection() as connection:
            latest_cycle = int(connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM learning_topic_method_attempts WHERE path_topic_id=?",
                (path_topic_id,),
            ).fetchone()[0])
            latest_job = connection.execute(
                "SELECT id FROM learning_jobs WHERE path_topic_id=? ORDER BY id DESC LIMIT 1",
                (path_topic_id,),
            ).fetchone()
            next_cycle = latest_cycle + 1
            if next_cycle > 3:
                return {"status": "blocked", "path_topic_id": path_topic_id, "reason": "method_cycle_limit_reached"}
            connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='pending', current_job_id=NULL, next_action='start_ai_learning',
                       diagnosis_json='{}', updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=?""",
                (path_topic_id,),
            )
            # Preserve every lesson/evidence column, but mark the previous cycle
            # as historical so no old completed job is mistaken for the active run.
            connection.execute(
                """UPDATE learning_jobs
                   SET status='superseded', updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=?
                     AND status IN ('completed', 'failed', 'rejected', 'cancelled')""",
                (path_topic_id,),
            )
            connection.execute(
                "UPDATE learning_path_topics SET status='in_progress', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (path_topic_id,),
            )
        return _queue_first_enabled_method(
            path_topic_id,
            attempt_number=next_cycle,
            source_job_id=int(latest_job["id"]) if latest_job is not None else None,
            lesson_review=lesson_review,
        )

    if gate["state"] == "passed":
        return {"status": "passed", "path_topic_id": path_topic_id}
    if gate["state"] == "blocked":
        return {"status": "blocked", "path_topic_id": path_topic_id}

    if gate["current_job_id"] is None:
        with learning_connection() as connection:
            latest_job = connection.execute(
                "SELECT id, status FROM learning_jobs WHERE path_topic_id=? ORDER BY id DESC LIMIT 1",
                (path_topic_id,),
            ).fetchone()
            if latest_job is not None and latest_job["status"] in {"queued", "running", "completed", "failed", "rejected"}:
                connection.execute(
                    """UPDATE learning_topic_quality_gates
                       SET current_job_id=?, state='pending', updated_at=CURRENT_TIMESTAMP
                       WHERE path_topic_id=? AND current_job_id IS NULL""",
                    (int(latest_job["id"]), path_topic_id),
                )
                gate = connection.execute(
                    "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
                    (path_topic_id,),
                ).fetchone()

    if gate["current_job_id"] is not None:
        current = learning.get_job(int(gate["current_job_id"]))
        if current["status"] in {"queued", "running"}:
            if current["status"] == "queued":
                task = learning._enqueue_learning_job(int(current["id"]))
            else:
                task = None
            return {
                "status": "already_active", "path_topic_id": path_topic_id,
                "method": current.get("learning_method"), "job": current, "task": task,
            }
        if current["status"] == "completed":
            identity = _method_attempt_identity(current)
            if identity is not None:
                _topic_id, method, attempt_number = identity
                passed_event = latest_attempt_event(path_topic_id, method, attempt_number)
                if passed_event is not None and passed_event["status"] == "passed":
                    try:
                        passed_evidence = json.loads(passed_event.get("evidence_json") or "{}")
                        passed_diagnosis = json.loads(passed_event.get("diagnosis_json") or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        passed_evidence, passed_diagnosis = {}, {}
                    evaluation_id = passed_evidence.get("evaluation_id") if isinstance(passed_evidence, dict) else None
                    if isinstance(evaluation_id, int) and isinstance(passed_event.get("score"), (int, float)) and isinstance(passed_event.get("content_sha256"), str):
                        return complete_method(
                            int(current["id"]),
                            score=float(passed_event["score"]),
                            evaluation_id=evaluation_id,
                            content_sha256=str(passed_event["content_sha256"]),
                            diagnosis=passed_diagnosis if isinstance(passed_diagnosis, dict) else {},
                            evidence=passed_evidence,
                        )
            evaluation = enqueue_topic_quality_evaluation(int(current["id"]))
            return {
                "status": "awaiting_evaluation", "path_topic_id": path_topic_id,
                "job": current, "evaluation": evaluation,
            }
        if current["status"] in {"failed", "rejected"}:
            if not current.get("method_attempt_key"):
                return _queue_first_enabled_method(
                    path_topic_id,
                    attempt_number=1,
                    source_job_id=int(current["id"]),
                )
            return record_generation_failure(
                int(current["id"]),
                reason=str(current.get("error") or "Learning method execution failed."),
            )

    events = list_attempt_events(path_topic_id)
    if events:
        last = events[-1]
        method = str(last["method"])
        attempt_number = int(last["attempt_number"])
        if last["status"] in {"queued", "running"}:
            method_key = f"path-topic:{path_topic_id}:method:{method}:attempt:{attempt_number}"
            job = _find_job_by_method_key(method_key)
            if job is not None:
                if job["status"] == "queued":
                    task = learning._enqueue_learning_job(int(job["id"]))
                else:
                    task = None
                return {"status": "resumed", "method": method, "job": job, "task": task}
        if last["status"] == "cancelled":
            return {"status": "cancelled", "path_topic_id": path_topic_id}
        if last["status"] in {"failed", "unavailable", "inconclusive"}:
            try:
                last_evidence = json.loads(last.get("evidence_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                last_evidence = {}
            if isinstance(last_evidence, dict) and last_evidence.get("cancelled_by_user") is True:
                return {"status": "cancelled", "path_topic_id": path_topic_id}
            if last["status"] == "unavailable" and not (
                isinstance(last_evidence, dict) and last_evidence.get("disabled_by_user_setting") is True
            ):
                return _block_topic(path_topic_id, reason="method_unavailable", evidence={"last_event": last})
            return _queue_next(path_topic_id, method, attempt_number, source_job_id=last.get("job_id"))
        if last["status"] == "passed":
            method_key = f"path-topic:{path_topic_id}:method:{method}:attempt:{attempt_number}"
            passed_job = _find_job_by_method_key(method_key)
            try:
                passed_evidence = json.loads(last.get("evidence_json") or "{}")
                passed_diagnosis = json.loads(last.get("diagnosis_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                passed_evidence, passed_diagnosis = {}, {}
            evaluation_id = passed_evidence.get("evaluation_id") if isinstance(passed_evidence, dict) else None
            if (
                passed_job is not None
                and isinstance(evaluation_id, int)
                and isinstance(last.get("score"), (int, float))
                and isinstance(last.get("content_sha256"), str)
            ):
                return complete_method(
                    int(passed_job["id"]),
                    score=float(last["score"]),
                    evaluation_id=evaluation_id,
                    content_sha256=str(last["content_sha256"]),
                    diagnosis=passed_diagnosis if isinstance(passed_diagnosis, dict) else {},
                    evidence=passed_evidence,
                )
            return _block_topic(path_topic_id, reason="passed_event_missing_recovery_evidence")

    return _queue_first_enabled_method(path_topic_id, attempt_number=1)

def _queue_first_enabled_method(path_topic_id: int, *, attempt_number: int, source_job_id: int | None = None, lesson_review: dict[str, Any] | None = None) -> dict[str, Any]:
    for method in METHOD_ORDER:
        if _method_enabled(method):
            return queue_method_attempt(path_topic_id, method, attempt_number=attempt_number, source_job_id=source_job_id, lesson_review=lesson_review)
        append_attempt_event(
            path_topic_id, method, attempt_number, "unavailable",
            event_key=f"path-topic:{path_topic_id}:method:{method}:attempt:{attempt_number}:unavailable",
            diagnosis={"reason": "disabled_by_user_setting"},
            evidence={"setting": f"{method}_enabled", "enabled": False, "disabled_by_user_setting": True},
        )
    return _block_topic(path_topic_id, reason="all_learning_methods_disabled")

def _queue_next(
    path_topic_id: int,
    method: str,
    attempt_number: int,
    *,
    source_job_id: int | None,
) -> dict[str, Any]:
    next_value = next_method(method)
    if next_value is None:
        return _block_topic(
            path_topic_id,
            reason="all_learning_methods_failed",
            evidence={"last_method": method, "attempt_number": attempt_number},
        )
    if not _method_enabled(next_value):
        append_attempt_event(
            path_topic_id, next_value, attempt_number, "unavailable",
            event_key=f"path-topic:{path_topic_id}:method:{next_value}:attempt:{attempt_number}:unavailable",
            diagnosis={"reason": "disabled_by_user_setting"},
            evidence={
                "setting": f"{next_value}_enabled",
                "enabled": False,
                "disabled_by_user_setting": True,
            },
        )
        return _queue_next(path_topic_id, next_value, attempt_number, source_job_id=source_job_id)
    return queue_method_attempt(
        path_topic_id, next_value,
        attempt_number=attempt_number,
        source_job_id=source_job_id,
    )
