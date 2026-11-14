"""Worker handlers for durable learning-job execution and recovery."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import learning, task_manager, ollama, learning_topic_quality_tasks
from . import learning_method_orchestrator, learning_execution
from .db import learning_connection
from .learning_escalations import _record_learning_escalation
from .task_ownership import LEARNING_JOB_TASK_TYPE
from .task_worker import PermanentTaskError, RetryableTaskError, TaskContext, TaskInterrupted

logger = logging.getLogger(__name__)
LEARNING_TASK_TYPE = LEARNING_JOB_TASK_TYPE
_TERMINAL_LEARNING_STATUSES = {"completed", "rejected", "cancelled"}


def _advance_failed_method(job_id: int, context: TaskContext, reason: str) -> dict[str, Any] | None:
    """Fallback only method-scoped curriculum jobs; preserve legacy job behavior."""
    job = learning.get_job(job_id)
    if not job.get("method_attempt_key"):
        return None
    if job.get("status") == "cancelled":
        learning_method_orchestrator.mark_method_cancelled(job_id, reason="Cancelled before method fallback.")
        return {"status": "cancelled", "job_id": job_id}
    result = learning_method_orchestrator.record_generation_failure(
        job_id,
        reason=reason,
        evidence={"failure_stage": "generation"},
        task_context=context,
    )
    if result.get("status") == "blocked":
        _record_learning_escalation(
            context.task_id, job_id, "all_learning_methods_failed",
            "All configured learning methods failed or were unavailable; the topic is blocked for review.",
        )
    return result


def handle_learning_job(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    """Run one persisted learning job under the task queue's lease and controls."""
    job_id = payload.get("job_id")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise PermanentTaskError("learning task payload requires a positive integer job_id")
    context.assert_current(
        expected_task_type=LEARNING_JOB_TASK_TYPE,
        expected_payload={"job_id": job_id},
    )

    try:
        job = learning.get_job(job_id)
    except KeyError as exc:
        raise PermanentTaskError(f"Learning job {job_id} does not exist") from exc

    status = job["status"]
    if status in {"failed", "rejected"} and job.get("method_attempt_key"):
        fallback = _advance_failed_method(
            job_id, context, str(job.get("error") or "Learning method failed before task recovery.")
        )
        if fallback is not None:
            return {"job_id": job_id, "learning_status": status, "method_fallback": fallback}
    if status in _TERMINAL_LEARNING_STATUSES:
        if status == "rejected":
            _record_learning_escalation(
                context.task_id, job_id, "quality_rejected", job.get("error") or "Lesson did not pass quality checks."
            )
        if status == "completed" and job.get("path_topic_id") is not None:
            evaluation = learning_topic_quality_tasks.enqueue_topic_quality_evaluation(job_id, task_context=context)
            if evaluation is None:
                learning_method_orchestrator.block_without_quality_gate(
                    int(job["path_topic_id"]), job_id=job_id, task_context=context
                )
        return {"job_id": job_id, "learning_status": status, "already_terminal": True}

    # A task is reclaimed only after its previous lease expires. A stale learning
    # status must therefore be reset before this newly claimed attempt can resume it.
    if status in {"failed", "running"}:
        with learning_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            context.assert_current(
                conn,
                expected_task_type=LEARNING_JOB_TASK_TYPE,
                expected_payload={"job_id": job_id},
            )
            current = conn.execute(
                "SELECT status, execution_owner FROM learning_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if current is None:
                raise PermanentTaskError(f"Learning job {job_id} disappeared during recovery.")
            if current["status"] == "running":
                owner = str(current["execution_owner"] or "")
                if owner and not owner.startswith(f"task:{context.task_id}:"):
                    old_task_id = None
                    if owner.startswith("task:"):
                        try:
                            old_task_id = int(owner.split(":", 2)[1])
                        except (TypeError, ValueError):
                            old_task_id = None
                    if old_task_id is None:
                        raise RetryableTaskError(
                            "Learning job is owned by a non-recoverable execution; refusing to steal it."
                        )
                    old_task = conn.execute(
                        "SELECT status, lease_expires_at FROM task_queue WHERE id=?",
                        (old_task_id,),
                    ).fetchone()
                    now = task_manager._timestamp()
                    if (
                        old_task is not None
                        and old_task["status"] in task_manager.ACTIVE_STATUSES
                        and (
                            old_task["lease_expires_at"] is None
                            or old_task["lease_expires_at"] > now
                        )
                    ):
                        raise RetryableTaskError(
                            "Learning job is still owned by another live task execution."
                        )
            conn.execute(
                """UPDATE learning_jobs
                   SET status='queued', execution_owner=NULL, error=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status IN ('failed', 'running')""",
                (job_id,),
            )
        job = learning.get_job(job_id)
        status = job["status"]

    if status != "queued":
        raise PermanentTaskError(
            f"Learning job {job_id} cannot run from status {status!r}"
        )

    context.checkpoint({"phase": "learning_queued", "job_id": job_id})
    try:
        context.checkpoint({"phase": "learning_job_start", "job_id": job_id})
        learning.run_job(job_id, task_context=context)
        context.checkpoint({"phase": "learning_job_finished", "job_id": job_id})
    except TaskInterrupted:
        cancelled_job = learning.get_job(job_id)
        if cancelled_job.get("status") == "cancelled" and cancelled_job.get("method_attempt_key"):
            learning_method_orchestrator.mark_method_cancelled(job_id)
        raise
    except learning.LearningError as exc:
        cause = exc.__cause__
        task = task_manager.get_task(context.task_id) or {}
        attempts = int(task.get("attempts", 0))
        max_attempts = int(task.get("max_attempts", 1))
        if isinstance(cause, ollama.OllamaError):
            if cause.retryable:
                if attempts >= max_attempts:
                    fallback = _advance_failed_method(job_id, context, f"Model request failed: {type(cause).__name__}")
                    if fallback is not None:
                        return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
                    _record_learning_escalation(
                        context.task_id, job_id, "attempts_exhausted",
                        f"Model request failed after {attempts} attempt(s): {type(cause).__name__}",
                    )
                raise RetryableTaskError("Learning model request failed; retry may succeed.") from exc
            fallback = _advance_failed_method(job_id, context, f"Non-retryable model error: {type(cause).__name__}")
            if fallback is not None:
                return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
            _record_learning_escalation(
                context.task_id, job_id, "permanent_failure",
                f"Non-retryable model error: {type(cause).__name__}",
            )
            raise PermanentTaskError("Learning model configuration or policy is invalid.") from exc
        if isinstance(cause, (ValueError, TypeError, KeyError, AssertionError, sqlite3.IntegrityError)):
            fallback = _advance_failed_method(job_id, context, f"Non-retryable learning error: {type(cause).__name__}")
            if fallback is not None:
                return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
            _record_learning_escalation(
                context.task_id, job_id, "permanent_failure",
                f"Non-retryable learning error: {type(cause).__name__}",
            )
            raise PermanentTaskError("Learning job failed because of invalid input or an internal invariant.") from exc
        if attempts >= max_attempts:
            fallback = _advance_failed_method(
                job_id, context,
                f"Learning execution failed: {type(cause).__name__ if cause else type(exc).__name__}",
            )
            if fallback is not None:
                return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
            _record_learning_escalation(
                context.task_id, job_id, "attempts_exhausted",
                f"Learning execution failed after {attempts} attempt(s): {type(cause).__name__ if cause else type(exc).__name__}",
            )
        raise RetryableTaskError("Learning job execution failed; retry may succeed.") from exc
    except learning_execution.LearningExecutionLeaseLostError as exc:
        raise RetryableTaskError("Learning job execution ownership changed; stale result was discarded.") from exc
    except Exception as exc:
        # Do not advance method state if this worker no longer owns the task lease.
        context.control_status()
        task = task_manager.get_task(context.task_id) or {}
        attempts = int(task.get("attempts", 0))
        max_attempts = int(task.get("max_attempts", 1))
        permanent = isinstance(
            exc, (ValueError, TypeError, KeyError, AssertionError, sqlite3.IntegrityError)
        )
        if permanent:
            fallback = _advance_failed_method(
                job_id, context, f"Non-retryable learning execution failure: {type(exc).__name__}"
            )
            if fallback is not None:
                return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
            _record_learning_escalation(
                context.task_id, job_id, "permanent_failure",
                f"Non-retryable learning execution failure: {type(exc).__name__}",
            )
            raise PermanentTaskError("Learning job failed because of invalid input or an internal invariant.") from exc
        if attempts >= max_attempts:
            fallback = _advance_failed_method(
                job_id, context, f"Unexpected learning execution failure: {type(exc).__name__}"
            )
            if fallback is not None:
                return {"job_id": job_id, "learning_status": "failed", "method_fallback": fallback}
            _record_learning_escalation(
                context.task_id, job_id, "attempts_exhausted",
                f"Unexpected learning execution failure after {attempts} attempt(s): {type(exc).__name__}",
            )
            raise PermanentTaskError("Learning execution failed after its bounded retry budget.") from exc
        raise RetryableTaskError("Learning execution failed unexpectedly; retry may succeed.") from exc

    result = learning.get_job(job_id)
    status = result["status"]
    if status == "cancelled" and result.get("method_attempt_key"):
        learning_method_orchestrator.mark_method_cancelled(job_id, reason="Learning job cancelled before quality evaluation.")
    if status == "rejected":
        _record_learning_escalation(
            context.task_id, job_id, "quality_rejected",
            result.get("error") or "Lesson did not pass quality checks.",
        )
    if status not in _TERMINAL_LEARNING_STATUSES:
        raise learning.LearningError(
            f"Learning job {job_id} returned without a terminal result (status={status!r})"
        )
    if status == "completed" and result.get("path_topic_id") is not None:
        evaluation = learning_topic_quality_tasks.enqueue_topic_quality_evaluation(job_id, task_context=context)
        if evaluation is None:
            learning_method_orchestrator.block_without_quality_gate(
                int(result["path_topic_id"]), job_id=job_id, task_context=context
            )
    return {"job_id": job_id, "learning_status": status, "already_terminal": False}
