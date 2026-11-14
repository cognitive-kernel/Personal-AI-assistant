"""Durable storage and queue publication for independent topic evaluation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import learning, task_manager
from .db import learning_connection
from .task_worker import TaskContext
from .task_ownership import LEARNING_JOB_TASK_TYPE, TOPIC_QUALITY_TASK_TYPE


def _content_hash(lesson: str) -> str:
    return hashlib.sha256(lesson.encode("utf-8")).hexdigest()


def enqueue_topic_quality_evaluation(
    job_id: int, *, task_context: TaskContext | None = None
) -> dict[str, Any] | None:
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
        if task_context is None:
            raise task_manager.TaskManagerError("A current learning task context is required.")
        task_context.assert_current(
            connection,
            expected_task_type=LEARNING_JOB_TASK_TYPE,
            expected_payload={"job_id": job_id},
        )
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


def _append_evaluation(
    record: dict[str, Any], *, outcome: str, score: float | None,
    task_context: TaskContext | None = None,
) -> int:
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
        if task_context is None:
            raise task_manager.TaskManagerError("A current topic-quality task context is required.")
        task_context.assert_current(
            connection,
            expected_task_type=TOPIC_QUALITY_TASK_TYPE,
            expected_payload={
                "job_id": job_id,
                "path_topic_id": path_topic_id,
                "content_sha256": record.get("content_sha256"),
            },
        )
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
