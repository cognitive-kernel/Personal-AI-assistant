"""Pure classification rules for autonomous workflow recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


MANUAL_TASK_PERSIST_STEP = (
    "Persist manual task under the controller's runner and concurrency ownership"
)
RETRYABLE = "retryable"
ADMISSION_REJECTED = "admission_rejected"
NON_EXECUTION = "non_execution"
UNKNOWN = "unknown"


def classify_failed_run(jobs: Sequence[Mapping[str, Any]]) -> str:
    """Classify a failed workflow from GitHub's structured job/step results.

    Only failures after the serialized task has entered the autonomous controller
    contribute to the retry circuit breaker. Admission and schedule-gate failures
    are not task-execution failures. Unknown shapes fail closed.
    """
    failed_jobs = [
        job for job in jobs
        if job.get("conclusion") in {"failure", "timed_out"}
    ]
    if not failed_jobs:
        return UNKNOWN

    if any(job.get("name") == "schedule-gate" for job in failed_jobs):
        return NON_EXECUTION if len(failed_jobs) == 1 else UNKNOWN

    codex_jobs = [job for job in failed_jobs if job.get("name") == "codex"]
    if len(codex_jobs) != 1 or len(failed_jobs) != 1:
        return UNKNOWN

    codex_job = codex_jobs[0]
    steps = codex_job.get("steps")
    if isinstance(steps, list):
        failed_steps = [
            step for step in steps
            if isinstance(step, Mapping)
            and step.get("conclusion") in {"failure", "timed_out"}
        ]
        admission_failures = [
            step for step in failed_steps
            if step.get("name") == MANUAL_TASK_PERSIST_STEP
        ]
        if admission_failures:
            return ADMISSION_REJECTED if len(failed_steps) == 1 else UNKNOWN
        if failed_steps:
            return RETRYABLE

    if codex_job.get("conclusion") == "timed_out":
        return RETRYABLE
    return UNKNOWN


def increment_retryable_failure_streak(streak: int, classification: str) -> int:
    """Advance the circuit-breaker counter using one classified failed run."""
    if isinstance(streak, bool) or not isinstance(streak, int) or streak < 0:
        raise ValueError("Failure streak must be a non-negative integer.")
    if classification in {ADMISSION_REJECTED, NON_EXECUTION}:
        return streak
    if classification == RETRYABLE:
        return streak + 1
    raise ValueError("Unknown workflow failure classification; recovery must fail closed.")
