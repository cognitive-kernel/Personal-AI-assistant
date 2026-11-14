"""Execution workflow for independent lesson evaluation and bounded repair."""

from __future__ import annotations

import json
from typing import Any

from . import learning, learning_topic_evaluator
from .db import learning_connection
from .task_worker import PermanentTaskError, TaskContext
from .task_ownership import TOPIC_QUALITY_TASK_TYPE
from .learning_topic_quality_store import _append_evaluation, _content_hash


def handle_topic_quality_evaluation(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_PAYLOAD_KEYS:
        raise PermanentTaskError("Topic quality evaluation payload is invalid.")
    job_id = payload.get("job_id")
    path_topic_id = payload.get("path_topic_id")
    expected_hash = payload.get("content_sha256")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (job_id, path_topic_id)):
        raise PermanentTaskError("Topic quality evaluation identifiers must be positive integers.")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise PermanentTaskError("Topic quality evaluation content hash is invalid.")
    context.assert_current(
        expected_task_type=TOPIC_QUALITY_TASK_TYPE,
        expected_payload={
            "job_id": job_id,
            "path_topic_id": path_topic_id,
            "content_sha256": expected_hash,
        },
    )
    job = learning.get_job(job_id)
    if job.get("status") != "completed" or job.get("path_topic_id") != path_topic_id:
        raise PermanentTaskError("Evaluation job does not own the requested completed curriculum topic.")
    if not isinstance(job.get("lesson"), str) or _content_hash(job["lesson"]) != expected_hash:
        raise PermanentTaskError("Lesson content changed after the evaluation task was queued.")
    with learning_connection() as connection:
        gate = connection.execute(
            "SELECT * FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
    if gate is None:
        raise PermanentTaskError("This topic is not enrolled in the sequential quality gate.")
    if gate["state"] == "passed":
        next_topic = learning.queue_next_researched_path_topic(path_topic_id)
        return {
            "path_topic_id": path_topic_id, "job_id": job_id,
            "status": "already_passed", "next_topic": next_topic,
        }
    if gate["state"] == "blocked":
        return {"path_topic_id": path_topic_id, "job_id": job_id, "status": "blocked"}
    if gate["current_job_id"] is None or int(gate["current_job_id"]) != job_id:
        if gate["state"] == "repairing" and gate["current_job_id"] is not None:
            current_job_id = int(gate["current_job_id"])
            current_job = learning.get_job(current_job_id)
            if current_job.get("status") == "queued":
                learning._enqueue_learning_job(current_job_id)
            return {
                "path_topic_id": path_topic_id, "job_id": job_id, "status": "repairing",
                "current_job_id": current_job_id,
                "recovered_correction_queue": current_job.get("status") == "queued",
            }
        raise PermanentTaskError("Evaluation task no longer owns the current topic attempt.")

    context.checkpoint({
        "phase": "topic_quality_evaluation_started",
        "path_topic_id": path_topic_id,
        "job_id": job_id,
        "content_sha256": expected_hash,
    })
    with learning_connection() as connection:
        previous = connection.execute(
            """SELECT id, outcome, result_json FROM learning_topic_quality_evaluations
               WHERE path_topic_id=? AND job_id=? AND content_sha256=?
               ORDER BY id DESC LIMIT 1""",
            (path_topic_id, job_id, expected_hash),
        ).fetchone()
    if previous is not None and previous["outcome"] in {"passed", "failed", "inconclusive"}:
        evaluation_id = int(previous["id"])
        record = json.loads(previous["result_json"])
        outcome = str(previous["outcome"])
    else:
        try:
            record = learning_topic_evaluator.evaluate_lesson(job_id)
        except Exception as exc:
            unavailable = {
                "version": learning_topic_evaluator.EVALUATOR_VERSION,
                "job_id": job_id,
                "path_topic_id": path_topic_id,
                "content_sha256": expected_hash,
                "evaluator_digest": "unavailable",
                "evaluated_at": learning_topic_evaluator._timestamp(),
                "score": None,
                "passed": False,
                "independent_evaluator": False,
                "outcome": "unavailable",
                "error_type": type(exc).__name__,
                "diagnosis": "Independent evaluator was unavailable; this topic cannot pass without an executed evaluation.",
            }
            context.checkpoint({
                "phase": "topic_quality_evaluation_unavailable",
                "path_topic_id": path_topic_id,
                "job_id": job_id,
                "content_sha256": expected_hash,
            })
            evaluation_id = _append_evaluation(
                unavailable, outcome="unavailable", score=None, task_context=context
            )
            if job.get("method_attempt_key"):
                from . import learning_method_orchestrator
                blocked = learning_method_orchestrator.block_unavailable(
                    job_id,
                    reason="independent_evaluator_unavailable",
                    evaluation_id=evaluation_id,
                    diagnosis=unavailable,
                    evidence={"evaluator_version": learning_topic_evaluator.EVALUATOR_VERSION},
                    task_context=context,
                )
                return {
                    "path_topic_id": path_topic_id, "job_id": job_id, "status": "blocked",
                    "reason": "independent_evaluator_unavailable", "evaluation_id": evaluation_id,
                    "method_state": blocked,
                }
            with learning_connection() as connection:
                connection.execute(
                    """UPDATE learning_topic_quality_gates
                       SET state='blocked', latest_evaluation_id=?, diagnosis_json=?,
                           next_action='configure_or_restore_independent_evaluator',
                           updated_at=CURRENT_TIMESTAMP
                       WHERE path_topic_id=? AND current_job_id=?""",
                    (evaluation_id, json.dumps(unavailable, ensure_ascii=False, sort_keys=True), path_topic_id, job_id),
                )
            return {
                "path_topic_id": path_topic_id, "job_id": job_id, "status": "blocked",
                "reason": "independent_evaluator_unavailable", "evaluation_id": evaluation_id,
            }

        outcome = "passed" if record.get("passed") is True else (
            "inconclusive" if record.get("evaluator_verdict") == "inconclusive" else "failed"
        )
        context.checkpoint({
            "phase": "topic_quality_evaluation_result_ready",
            "path_topic_id": path_topic_id,
            "job_id": job_id,
            "content_sha256": expected_hash,
        })
        evaluation_id = _append_evaluation(
            record, outcome=outcome, score=record.get("score"), task_context=context
        )
    if job.get("method_attempt_key"):
        from . import learning_method_orchestrator
        if outcome == "passed":
            transition = learning_method_orchestrator.complete_method(
                job_id,
                score=float(record["score"]),
                evaluation_id=evaluation_id,
                content_sha256=expected_hash,
                diagnosis=record,
                evidence={
                    "evaluator_id": record.get("evaluator_digest"),
                    "evaluator_version": record.get("version"),
                    "independent_evaluator": record.get("independent_evaluator") is True,
                },
                task_context=context,
            )
        else:
            transition = learning_method_orchestrator.advance_after_failure(
                job_id,
                reason=str(record.get("overall_diagnosis") or "Independent lesson evaluation did not pass."),
                score=record.get("score"),
                diagnosis=record,
                evidence={
                    "evaluation_id": evaluation_id,
                    "evaluator_id": record.get("evaluator_digest"),
                    "evaluator_version": record.get("version"),
                    "independent_evaluator": record.get("independent_evaluator") is True,
                },
                outcome="inconclusive" if outcome == "inconclusive" else "failed",
                task_context=context,
            )
        return {
            "path_topic_id": path_topic_id, "job_id": job_id,
            "status": "passed" if outcome == "passed" else transition.get("status", "blocked"),
            "score": record.get("score"), "evaluation_id": evaluation_id,
            "method_transition": transition,
        }
    diagnosis_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if outcome == "passed":
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            context.assert_current(
                connection,
                expected_task_type=TOPIC_QUALITY_TASK_TYPE,
                expected_payload={
                    "job_id": job_id,
                    "path_topic_id": path_topic_id,
                    "content_sha256": expected_hash,
                },
            )
            updated = connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='passed', latest_score=?, latest_content_sha256=?,
                       latest_evaluation_id=?, diagnosis_json=?, next_action='queue_next_topic',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=? AND current_job_id=? AND state!='passed'""",
                (record["score"], expected_hash, evaluation_id, diagnosis_json, path_topic_id, job_id),
            )
            if updated.rowcount:
                connection.execute(
                    """UPDATE learning_path_topics
                       SET status='completed', updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (path_topic_id,),
                )
        if updated.rowcount:
            next_result = learning.queue_next_researched_path_topic(path_topic_id)
        else:
            next_result = None
        return {
            "path_topic_id": path_topic_id, "job_id": job_id, "status": "passed",
            "score": record["score"], "evaluation_id": evaluation_id,
            "next_topic": next_result,
        }

    repair_count = int(gate["repair_count"])
    max_repairs = int(gate["max_repairs"])
    try:
        prior_diagnosis = json.loads(gate["diagnosis_json"] or "{}")
    except (TypeError, ValueError):
        prior_diagnosis = {}
    repair_already_counted = (
        gate["state"] == "repairing"
        and isinstance(prior_diagnosis, dict)
        and prior_diagnosis.get("evaluation_id") == evaluation_id
    )
    if repair_already_counted:
        # A worker may restart after recording the diagnosis but before creating
        # the correction job. Reuse the same idempotency key without spending a
        # second repair allowance.
        pass
    elif repair_count >= max_repairs:
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            context.assert_current(
                connection,
                expected_task_type=TOPIC_QUALITY_TASK_TYPE,
                expected_payload={
                    "job_id": job_id,
                    "path_topic_id": path_topic_id,
                    "content_sha256": expected_hash,
                },
            )
            connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='blocked', latest_score=?, latest_content_sha256=?,
                       latest_evaluation_id=?, diagnosis_json=?, next_action='manual_or_new_evidence_required',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=? AND current_job_id=?""",
                (record["score"], expected_hash, evaluation_id, diagnosis_json, path_topic_id, job_id),
            )
            connection.execute(
                "UPDATE learning_path_topics SET status='needs_review', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (path_topic_id,),
            )
        return {
            "path_topic_id": path_topic_id, "job_id": job_id, "status": "blocked",
            "score": record["score"], "evaluation_id": evaluation_id,
            "repair_count": repair_count, "max_repairs": max_repairs,
        }

    diagnosis = {
        "evaluation_id": evaluation_id,
        "score": record["score"],
        "threshold": record["pass_threshold"],
        "overall_diagnosis": record["overall_diagnosis"],
        "critical_failures": record["critical_failures"],
        "failed_dimensions": {
            name: item for name, item in record["dimensions"].items()
            if item.get("failed_checks")
        },
        "repair_instructions": record["repair_instructions"],
        "preserve_original": True,
    }
    issue_report = json.dumps(diagnosis, ensure_ascii=False, sort_keys=True)
    request_key = f"topic-quality:{path_topic_id}:{expected_hash}:{evaluation_id}"
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        context.assert_current(
                connection,
                expected_task_type=TOPIC_QUALITY_TASK_TYPE,
                expected_payload={
                    "job_id": job_id,
                    "path_topic_id": path_topic_id,
                    "content_sha256": expected_hash,
                },
            )
        if not repair_already_counted:
            connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='repairing', repair_count=repair_count+1,
                       latest_score=?, latest_content_sha256=?, latest_evaluation_id=?,
                       diagnosis_json=?, next_action='create_or_resume_versioned_repair',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=? AND current_job_id=? AND state!='passed'""",
                (record["score"], expected_hash, evaluation_id, diagnosis_json, path_topic_id, job_id),
            )
    context.checkpoint({
        "phase": "topic_quality_repair_authorized",
        "path_topic_id": path_topic_id,
        "job_id": job_id,
        "evaluation_id": evaluation_id,
    })
    correction = learning.submit_lesson_issue(
        job_id, issue_report, request_key=request_key, enqueue=False
    )
    correction_job_id = int(correction["correction_job"]["id"])
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        context.assert_current(
                connection,
                expected_task_type=TOPIC_QUALITY_TASK_TYPE,
                expected_payload={
                    "job_id": job_id,
                    "path_topic_id": path_topic_id,
                    "content_sha256": expected_hash,
                },
            )
        updated = connection.execute(
            """UPDATE learning_topic_quality_gates
               SET current_job_id=?, state='repairing', next_action='evaluate_corrected_lesson',
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND current_job_id=?""",
            (correction_job_id, path_topic_id, job_id),
        )
        if updated.rowcount != 1:
            raise PermanentTaskError("The current topic attempt changed before repair ownership was committed.")
    # Publish the correction only after durable gate ownership points at it. A
    # fast worker can therefore never finish before the gate recognizes its job.
    correction_task = learning._enqueue_learning_job(correction_job_id)
    context.checkpoint({
        "phase": "topic_quality_repair_queued",
        "path_topic_id": path_topic_id,
        "failed_job_id": job_id,
        "correction_job_id": correction_job_id,
        "evaluation_id": evaluation_id,
    })
    return {
        "path_topic_id": path_topic_id, "job_id": job_id, "status": "repairing",
        "score": record["score"], "evaluation_id": evaluation_id,
        "correction_job_id": correction_job_id,
        "correction_task_id": correction_task["id"],
        "repair_count": repair_count if repair_already_counted else repair_count + 1,
        "max_repairs": max_repairs,
    }
