from __future__ import annotations

import pytest

from scripts.autonomous_engineering_recovery_policy import (
    ADMISSION_REJECTED,
    NON_EXECUTION,
    RETRYABLE,
    UNKNOWN,
    MANUAL_TASK_PERSIST_STEP,
    classify_failed_run,
    retryable_failure_streak,
)


def _job(name: str, conclusion: str, failed_step: str | None = None) -> dict:
    steps = []
    if failed_step:
        steps.append({"name": failed_step, "conclusion": "failure"})
    return {"name": name, "conclusion": conclusion, "steps": steps}


def test_manual_task_slot_rejection_is_not_an_execution_failure():
    jobs = [_job("schedule-gate", "success"), _job("codex", "failure", MANUAL_TASK_PERSIST_STEP)]

    assert classify_failed_run(jobs) == ADMISSION_REJECTED


def test_schedule_gate_failure_is_not_an_execution_failure():
    jobs = [_job("schedule-gate", "failure", "Apply the configured daily execution frequency")]

    assert classify_failed_run(jobs) == NON_EXECUTION


def test_failure_after_controller_task_execution_starts_is_retryable():
    jobs = [_job("schedule-gate", "success"), _job("codex", "failure", "Execute one isolated task, validate, and publish for owner review")]

    assert classify_failed_run(jobs) == RETRYABLE


def test_codex_job_timeout_is_retryable_even_without_a_failed_step():
    jobs = [_job("codex", "timed_out")]

    assert classify_failed_run(jobs) == RETRYABLE


def test_ambiguous_failed_job_shape_fails_closed():
    assert classify_failed_run([_job("unknown-job", "failure", "some step")]) == UNKNOWN
    assert classify_failed_run([_job("codex", "failure")]) == UNKNOWN


def test_failure_streak_ignores_admission_rejections_but_counts_execution_failures():
    runs = [
        {"id": 10, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
        {"id": 9, "run_number": 9, "head_branch": "main", "conclusion": "failure"},
        {"id": 8, "run_number": 8, "head_branch": "main", "conclusion": "failure"},
        {"id": 7, "run_number": 7, "head_branch": "main", "conclusion": "success"},
    ]
    jobs = {
        10: [_job("codex", "failure", "Execute one isolated task, validate, and publish for owner review")],
        9: [_job("codex", "failure", MANUAL_TASK_PERSIST_STEP)],
        8: [_job("codex", "failure", "Verify local Ollama and Codex prerequisites")],
    }

    assert retryable_failure_streak(runs, jobs, failed_run_id=10) == 2


def test_success_resets_execution_failure_streak():
    runs = [
        {"id": 12, "run_number": 12, "head_branch": "main", "conclusion": "failure"},
        {"id": 11, "run_number": 11, "head_branch": "main", "conclusion": "success"},
        {"id": 10, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
    ]
    jobs = {
        12: [_job("codex", "failure", "Execute one isolated task, validate, and publish for owner review")],
    }

    assert retryable_failure_streak(runs, jobs, failed_run_id=12) == 1


def test_unknown_historical_failure_blocks_recovery():
    runs = [
        {"id": 10, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
        {"id": 9, "run_number": 9, "head_branch": "main", "conclusion": "failure"},
    ]
    jobs = {
        10: [_job("codex", "failure", "Execute one isolated task, validate, and publish for owner review")],
        9: [_job("unexpected", "failure", "unknown")],
    }

    with pytest.raises(ValueError, match="Unknown workflow failure"):
        retryable_failure_streak(runs, jobs, failed_run_id=10)


def test_triggering_run_must_exist_in_main_history():
    with pytest.raises(ValueError, match="missing from history"):
        retryable_failure_streak([], {}, failed_run_id=123)
