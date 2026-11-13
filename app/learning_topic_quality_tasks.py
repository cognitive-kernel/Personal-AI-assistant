"""Durable sequential quality gate for researched curriculum topics."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import learning, learning_topic_evaluator, task_manager
from .db import learning_connection
from .task_worker import PermanentTaskError, TaskContext

TOPIC_QUALITY_TASK_TYPE = "learning.evaluate_topic_quality"
_ALLOWED_PAYLOAD_KEYS = {"job_id", "path_topic_id", "content_sha256"}


def _content_hash(lesson: str) -> str:
    return hashlib.sha256(lesson.encode("utf-8")).hexdigest()


def enqueue_topic_quality_evaluation(job_id: int) -> dict[str, Any] | None:
    """Queue evaluation only for topics explicitly enrolled in the new quality gate."""
    job = learning.get_job(job_id)
    path_topic_id = job.get("path_topic_id")
    if path_topic_id is None or job.get("status") != "completed":
        return None
    lesson = job.get("lesson")
    if not isinstance(lesson, str) or not lesson.strip():
        raise ValueError("A non-empty completed lesson is required for topic evaluation.")
    digest = _content_hash(lesson)
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()
        if gate is None:
            return None
        if gate["state"] == "passed":
            connection.commit()
            next_topic = learning.queue_next_researched_path_topic(int(path_topic_id))
            return {
                "status": "already_passed", "path_topic_id": int(path_topic_id),
                "next_topic": next_topic,
            }
        current_job_id = gate["current_job_id"]
        if current_job_id is not None and int(current_job_id) != job_id:
            return {"status": "owned_by_another_job", "path_topic_id": int(path_topic_id)}
        if gate["state"] == "blocked":
            return {"status": "blocked", "path_topic_id": int(path_topic_id)}
        connection.execute(
            """UPDATE learning_topic_quality_gates
               SET current_job_id=?, state='evaluating', latest_content_sha256=?,
                   next_action='run_independent_evaluation', updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND state IN ('pending','repairing','evaluating')""",
            (job_id, digest, path_topic_id),
        )
    key = f"topic-quality:{path_topic_id}:{job_id}:{digest}"
    task = task_manager.create_task(
        TOPIC_QUALITY_TASK_TYPE,
        {"job_id": job_id, "path_topic_id": int(path_topic_id), "content_sha256": digest},
        priority=0,
        max_attempts=2,
        dedupe_key=key,
    )
    return {"status": "queued", "path_topic_id": int(path_topic_id), "task_id": task["id"]}


def _append_evaluation(record: dict[str, Any], *, outcome: str, score: float | None) -> int:
    path_topic_id = record.get("path_topic_id")
    job_id = record.get("job_id")
    if isinstance(path_topic_id, bool) or not isinstance(path_topic_id, int):
        raise ValueError("Evaluation record has no valid path topic.")
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        raise ValueError("Evaluation record has no valid learning job.")
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        sequence = int(connection.execute(
            "SELECT COALESCE(MAX(evaluation_sequence), 0) + 1 FROM learning_topic_quality_evaluations WHERE path_topic_id=?",
            (path_topic_id,),
        ).fetchone()[0])
        cursor = connection.execute(
            """INSERT INTO learning_topic_quality_evaluations(
                   path_topic_id, job_id, evaluation_sequence, content_sha256,
                   evaluator_id, evaluator_version, score, outcome, result_json,
                   result_sha256, evaluated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                path_topic_id, job_id, sequence, record["content_sha256"],
                str(record.get("evaluator_digest") or "unavailable")[:256],
                str(record.get("version") or learning_topic_evaluator.EVALUATOR_VERSION)[:120],
                score, outcome, canonical, digest, str(record.get("evaluated_at") or learning_topic_evaluator._timestamp()),
            ),
        )
        return int(cursor.lastrowid)


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
            evaluation_id = _append_evaluation(unavailable, outcome="unavailable", score=None)
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
        evaluation_id = _append_evaluation(record, outcome=outcome, score=record.get("score"))
    diagnosis_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if outcome == "passed":
        with learning_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
                       SET status='completed', latest_score=?, latest_score_kind='topic_quality_gate_v1',
                           latest_score_source='independent_topic_evaluator',
                           updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (record["score"], path_topic_id),
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
            connection.execute(
                """UPDATE learning_topic_quality_gates
                   SET state='blocked', latest_score=?, latest_content_sha256=?,
                       latest_evaluation_id=?, diagnosis_json=?, next_action='manual_or_new_evidence_required',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE path_topic_id=? AND current_job_id=?""",
                (record["score"], expected_hash, evaluation_id, diagnosis_json, path_topic_id, job_id),
            )
            connection.execute(
                "UPDATE learning_path_topics SET status='needs_review', latest_score=?, latest_score_kind='topic_quality_gate_v1', latest_score_source='independent_topic_evaluator', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (record["score"], path_topic_id),
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
    correction = learning.submit_lesson_issue(
        job_id, issue_report, request_key=request_key, enqueue=False
    )
    correction_job_id = int(correction["correction_job"]["id"])
    with learning_connection() as connection:
        connection.execute(
            """UPDATE learning_topic_quality_gates
               SET current_job_id=?, state='repairing', next_action='evaluate_corrected_lesson',
                   updated_at=CURRENT_TIMESTAMP
               WHERE path_topic_id=? AND current_job_id=?""",
            (correction_job_id, path_topic_id, job_id),
        )
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
