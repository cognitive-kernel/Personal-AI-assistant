"""Pure classification rules for autonomous workflow recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


MANUAL_TASK_PERSIST_STEP = (
    "Persist manual task under the controller's runner and concurrency ownership"
)
TASK_EXECUTION_STEP = "Execute one isolated task, validate, and publish for owner review"
RETRYABLE = "retryable"
ADMISSION_REJECTED = "admission_rejected"
NON_EXECUTION = "non_execution"
UNKNOWN = "unknown"


def classify_failed_run(jobs: Sequence[Mapping[str, Any]]) -> str:
    """Classify a failed workflow using its exact job/step execution boundary.

    Only a failure reported by the controller's task-execution step contributes
    to the execution-failure circuit breaker. Admission, schedule-gate,
    prerequisite, sandbox-smoke, dependency-install, and post-execution failures
    do not prove that a task execution failed and therefore do not increment it.
    Unknown or malformed GitHub response shapes fail closed.
    """
    if not isinstance(jobs, Sequence) or isinstance(jobs, (str, bytes)):
        return UNKNOWN
    if any(not isinstance(job, Mapping) for job in jobs):
        return UNKNOWN

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

    steps = codex_jobs[0].get("steps")
    if not isinstance(steps, list):
        return UNKNOWN
    if any(
        not isinstance(step, Mapping)
        or not isinstance(step.get("name"), str)
        or not step.get("name")
        for step in steps
    ):
        return UNKNOWN

    failed_steps = [
        step for step in steps
        if step.get("conclusion") in {"failure", "timed_out"}
    ]
    admission_failures = [
        step for step in failed_steps
        if step.get("name") == MANUAL_TASK_PERSIST_STEP
    ]
    if admission_failures:
        return ADMISSION_REJECTED if len(failed_steps) == 1 else UNKNOWN

    execution_failures = [
        step for step in failed_steps
        if step.get("name") == TASK_EXECUTION_STEP
    ]
    if execution_failures:
        return RETRYABLE if len(execution_failures) == 1 and len(failed_steps) == 1 else UNKNOWN

    # A failed preflight or postcondition is operationally important, but it
    # is not evidence that the coding task itself started and failed. Do not
    # consume the task-execution retry budget or dispatch a duplicate task.
    if failed_steps:
        return NON_EXECUTION

    # A job-level timeout without a failed step cannot establish which phase
    # owned the timeout. Treat it as unknown rather than guessing.
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


def retryable_failure_streak(
    runs: Sequence[Mapping[str, Any]],
    jobs_by_run: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    failed_run_id: int,
) -> int:
    """Evaluate a finite history slice using the same transition contract as recovery."""
    ordered = sorted(
        (run for run in runs if run.get("head_branch") == "main"),
        key=lambda run: int(run.get("run_number", 0)),
        reverse=True,
    )
    start = next(
        (index for index, run in enumerate(ordered)
         if int(run.get("id", 0)) == failed_run_id),
        None,
    )
    if start is None:
        raise ValueError("The triggering failed workflow run is missing from history.")

    streak = 0
    for run in ordered[start:]:
        conclusion = run.get("conclusion")
        if conclusion in {"success", "cancelled"}:
            break
        if conclusion not in {"failure", "timed_out"}:
            continue
        classification = classify_failed_run(jobs_by_run.get(int(run.get("id", 0)), ()))
        streak = increment_retryable_failure_streak(streak, classification)
    return streak
