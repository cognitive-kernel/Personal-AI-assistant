from __future__ import annotations

from scripts import autonomous_engineering_recovery as recovery
from scripts.autonomous_engineering_recovery_policy import (
    MANUAL_TASK_PERSIST_STEP,
)


def _failed_admission_jobs():
    return [
        {"name": "schedule-gate", "conclusion": "success", "steps": []},
        {
            "name": "codex",
            "conclusion": "failure",
            "steps": [{"name": MANUAL_TASK_PERSIST_STEP, "conclusion": "failure"}],
        },
    ]


def _failed_execution_jobs():
    return [
        {"name": "schedule-gate", "conclusion": "success", "steps": []},
        {
            "name": "codex",
            "conclusion": "failure",
            "steps": [{"name": "Execute one isolated task, validate, and publish for owner review", "conclusion": "failure"}],
        },
    ]


def _configure_environment(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "continuous")
    monkeypatch.setenv("PAUSED", "false")
    monkeypatch.setenv("GH_REPO", "cognitive-kernel/Personal-AI-assistant")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("FAILED_RUN_ID", "123")


def test_main_blocks_admission_rejection_without_dispatch(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return _failed_admission_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 0

        def request(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert calls == []
    assert "task-admission rejection" in reports[0][0][1]


def test_main_opens_breaker_as_a_failed_recovery_result(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 3

        def request(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert calls == []
    assert reports[0][1]["error"] is True
    assert "CIRCUIT BREAKER OPEN" in reports[0][0][1]


def test_main_dispatches_only_an_empty_recovery_task_below_threshold(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 1

        def request(self, method, path, payload=None):
            calls.append((method, path, payload))
            return {}

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: None)

    assert recovery.main() == 0
    assert calls == [
        (
            "POST",
            "/actions/workflows/autonomous-codex-engineering.yml/dispatches",
            {"ref": "main", "inputs": {"manual_task": "", "recovery_attempt": "1"}},
        )
    ]


def test_main_honors_scheduled_mode_and_durable_pause_before_api_access(monkeypatch):
    _configure_environment(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "scheduled")
    monkeypatch.setattr(recovery, "GitHubActionsClient", lambda *args: (_ for _ in ()).throw(AssertionError("API must not be called")))
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: None)

    assert recovery.main() == 0

    monkeypatch.setenv("EXECUTION_MODE", "continuous")
    monkeypatch.setenv("PAUSED", "true")
    assert recovery.main() == 0


def test_history_counter_uses_job_contracts_and_ignores_admission_failures():
    class FakeClient(recovery.GitHubActionsClient):
        def __init__(self):
            pass

        def request(self, method, path, payload=None):
            assert method == "GET"
            return {
                "workflow_runs": [
                    {"id": 123, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
                    {"id": 122, "run_number": 9, "head_branch": "main", "conclusion": "failure"},
                    {"id": 121, "run_number": 8, "head_branch": "main", "conclusion": "success"},
                ]
            }

        def jobs(self, run_id):
            if run_id == 122:
                return _failed_admission_jobs()
            raise AssertionError("Current jobs are supplied by the caller.")

    client = FakeClient()
    assert client.retryable_failure_streak(
        123,
        _failed_execution_jobs(),
    ) == 1


def test_history_counter_fails_closed_on_unknown_previous_failure():
    class FakeClient(recovery.GitHubActionsClient):
        def __init__(self):
            pass

        def request(self, method, path, payload=None):
            return {
                "workflow_runs": [
                    {"id": 123, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
                    {"id": 122, "run_number": 9, "head_branch": "main", "conclusion": "failure"},
                ]
            }

        def jobs(self, run_id):
            return [{"name": "unknown-job", "conclusion": "failure", "steps": []}]

    client = FakeClient()
    import pytest
    with pytest.raises(RuntimeError, match="Cannot safely classify failed workflow run 122"):
        client.retryable_failure_streak(123, _failed_execution_jobs())


def test_current_admission_rejection_does_not_hide_an_already_open_breaker(monkeypatch):
    _configure_environment(monkeypatch)
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return _failed_admission_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 3

        def request(self, *args, **kwargs):
            raise AssertionError("An open breaker must not dispatch.")

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert reports[0][1]["error"] is True
    assert "CIRCUIT BREAKER OPEN" in reports[0][0][1]
