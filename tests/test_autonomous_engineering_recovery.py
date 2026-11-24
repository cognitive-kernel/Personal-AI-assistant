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
    monkeypatch.setenv("GH_REPO", "cognitive-kernel/Personal-AI-assistant")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "settings-token")
    monkeypatch.setenv("FAILED_RUN_ID", "123")


def test_main_requires_dedicated_variables_credential(monkeypatch):
    _configure_environment(monkeypatch)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN")
    reports = []
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert reports
    assert "read-only Variables credential" in reports[0][0][1]


def test_github_actions_client_separates_workflow_and_settings_credentials(monkeypatch):
    authorizations = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        authorizations.append(request.get_header("Authorization"))
        return Response()

    monkeypatch.setattr(recovery.urllib.request, "urlopen", fake_urlopen)
    client = recovery.GitHubActionsClient("owner/repository", "workflow-token")
    client.set_settings_token("settings-token")

    client.request("GET", "/actions/runs")
    client.request_settings("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE")
    try:
        client.request_settings("POST", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE")
    except RuntimeError as exc:
        assert "only GET requests" in str(exc)
    else:
        raise AssertionError("The read-only settings credential accepted a write request.")

    assert authorizations == ["Bearer workflow-token", "Bearer settings-token"]


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
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
            calls.append((args, kwargs))

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert calls == []
    assert "task-admission rejection" in reports[0][0][1]


def test_main_does_not_dispatch_for_a_preflight_failure(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return [
                {"name": "schedule-gate", "conclusion": "success", "steps": []},
                {
                    "name": "codex",
                    "conclusion": "failure",
                    "steps": [{"name": "Verify local Ollama and Codex prerequisites", "conclusion": "failure"}],
                },
            ]

        def retryable_failure_streak(self, run_id, jobs):
            return 0

        def request(self, *args, **kwargs):
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
            calls.append((args, kwargs))

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert calls == []
    assert "before task execution" in reports[0][0][1]


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
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
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
            if method == "GET" and path == "/":
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE":
                return {"value": "continuous"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED":
                return {"value": "false"}
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


def test_main_honors_live_scheduled_mode_and_durable_pause(monkeypatch):
    _configure_environment(monkeypatch)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: None)

    class ScheduledClient:
        def __init__(self, repository, token):
            pass

        def request(self, method, path, payload=None):
            assert method == "GET"
            if path == "/":
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            assert path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"
            return {"value": "scheduled"}

    monkeypatch.setattr(recovery, "GitHubActionsClient", ScheduledClient)
    assert recovery.main() == 0

    # main() intentionally consumes the read-only credential from the environment.
    _configure_environment(monkeypatch)

    class PausedClient:
        def __init__(self, repository, token):
            pass

        def request(self, method, path, payload=None):
            assert method == "GET"
            if path == "/":
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE":
                return {"value": "continuous"}
            if path == "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED":
                return {"value": "true"}
            raise AssertionError(path)

    monkeypatch.setattr(recovery, "GitHubActionsClient", PausedClient)
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
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
            raise AssertionError("An open breaker must not dispatch.")

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 1
    assert reports[0][1]["error"] is True
    assert "CIRCUIT BREAKER OPEN" in reports[0][0][1]


def test_stale_recovery_event_is_superseded_by_a_newer_main_run():
    class FakeClient(recovery.GitHubActionsClient):
        def __init__(self):
            pass

        def request(self, method, path, payload=None):
            return {
                "workflow_runs": [
                    {"id": 124, "run_number": 11, "head_branch": "main", "conclusion": "in_progress"},
                    {"id": 123, "run_number": 10, "head_branch": "main", "conclusion": "failure"},
                ]
            }

    client = FakeClient()
    import pytest
    with pytest.raises(recovery.RecoverySuperseded, match="newer main-branch workflow run"):
        client.retryable_failure_streak(123, _failed_execution_jobs())


def test_main_does_not_dispatch_for_a_superseded_recovery_event(monkeypatch):
    _configure_environment(monkeypatch)
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            pass

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            raise recovery.RecoverySuperseded("A newer main-branch workflow run exists.")

        def request(self, *args, **kwargs):
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
            raise AssertionError("A superseded event must not dispatch.")

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 0
    assert "Recovery superseded" == reports[0][0][0]


def test_main_rechecks_ownership_after_backoff_before_dispatch(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            self.checks = 0

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            self.checks += 1
            if self.checks == 1:
                return 1
            raise recovery.RecoverySuperseded("A newer main-branch workflow run appeared during backoff.")

        def request(self, *args, **kwargs):
            if args[:2] == ("GET", "/"):
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_MODE"):
                return {"value": "continuous"}
            if args[:2] == ("GET", "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"):
                return {"value": "false"}
            calls.append((args, kwargs))

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 0
    assert calls == []
    assert reports[-1][0][0] == "Recovery superseded"


def test_report_appends_summaries_and_emits_truthful_annotations(monkeypatch, tmp_path, capsys):
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    recovery.report("Recovery scheduled", "No task state was changed.")
    recovery.report("Circuit breaker OPEN", "No dispatch occurred.", error=True)

    output = capsys.readouterr().out
    saved = summary.read_text(encoding="utf-8")
    assert "::notice title=Recovery scheduled::" in output
    assert "::error title=Circuit breaker OPEN::" in output
    assert "## Recovery scheduled" in saved
    assert "## Circuit breaker OPEN" in saved


def test_live_pause_missing_variable_defaults_false():
    class MissingVariableClient:
        def request(self, method, path, payload=None):
            raise RuntimeError("GitHub Actions API failed: HTTP 404: not found")

    assert recovery._live_pause_enabled(MissingVariableClient()) is False


def test_live_pause_api_failure_fails_closed():
    import pytest

    class DeniedClient:
        def request(self, method, path, payload=None):
            raise RuntimeError("GitHub Actions API failed: HTTP 403: forbidden")

    with pytest.raises(RuntimeError, match="Could not verify the live durable Pause setting"):
        recovery._live_pause_enabled(DeniedClient())


def test_main_rechecks_live_pause_after_backoff_before_dispatch(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            self.pause_reads = 0

        def request(self, method, path, payload=None):
            if method == "GET" and path == "/":
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE":
                return {"value": "continuous"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED":
                self.pause_reads += 1
                return {"value": "false" if self.pause_reads == 1 else "true"}
            calls.append((method, path, payload))
            return {}

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 1

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 0
    assert calls == []
    assert reports[-1][0][0] == "Recovery paused"
    assert "became active during backoff" in reports[-1][0][1]


def test_live_execution_mode_missing_variable_defaults_continuous():
    class MissingVariableClient:
        def request(self, method, path, payload=None):
            raise RuntimeError("GitHub Actions API failed: HTTP 404: not found")

    assert recovery._live_execution_mode(MissingVariableClient()) == "continuous"


def test_live_execution_mode_api_failure_fails_closed():
    import pytest

    class DeniedClient:
        def request(self, method, path, payload=None):
            raise RuntimeError("GitHub Actions API failed: HTTP 403: forbidden")

    with pytest.raises(RuntimeError, match="Could not verify the live execution mode"):
        recovery._live_execution_mode(DeniedClient())


def test_main_rechecks_live_execution_mode_after_backoff_before_dispatch(monkeypatch):
    _configure_environment(monkeypatch)
    calls = []
    reports = []

    class FakeClient:
        def __init__(self, repository, token):
            self.mode_reads = 0

        def request(self, method, path, payload=None):
            if method == "GET" and path == "/":
                return {"full_name": "cognitive-kernel/Personal-AI-assistant"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_MODE":
                self.mode_reads += 1
                return {"value": "continuous" if self.mode_reads == 1 else "scheduled"}
            if method == "GET" and path == "/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED":
                return {"value": "false"}
            calls.append((method, path, payload))
            return {}

        def jobs(self, run_id):
            return _failed_execution_jobs()

        def retryable_failure_streak(self, run_id, jobs):
            return 1

    monkeypatch.setattr(recovery, "GitHubActionsClient", FakeClient)
    monkeypatch.setattr(recovery.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(recovery, "report", lambda *args, **kwargs: reports.append((args, kwargs)))

    assert recovery.main() == 0
    assert calls == []
    assert reports[-1][0][0] == "Recovery not enabled"
    assert "changed to scheduled during backoff" in reports[-1][0][1]
