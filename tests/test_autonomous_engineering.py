from __future__ import annotations

import asyncio
import io
import json
from contextlib import contextmanager, nullcontext
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import autonomous_engineering as agent
from app import main as main_module


@pytest.fixture(autouse=True)
def _stub_durable_pending_work_status(monkeypatch):
    monkeypatch.setattr(agent, "_LAST_DISPATCH_ACCEPTED_AT", 0.0)

    @contextmanager
    def fake_dispatch_admission_lock():
        yield {"accepted": False, "last_accepted_at": 0.0}

    monkeypatch.setattr(agent, "dispatch_admission_lock", fake_dispatch_admission_lock)
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": True,
        "blocked": False,
        "markers": {
            "pending_task": False,
            "pending_owner": False,
            "pending_pr": False,
            "cleanup_in_progress": False,
        },
        "checkpoint": {"available": False, "verified": True},
        "reason": None,
    })



def test_dispatch_admission_context_uses_broker_reservation_and_commits_acceptance(monkeypatch):
    from app import autonomous_engineering_dispatch_lock as dispatch_lock

    calls = []
    def fake_request(method, path, *, timeout_ms=3500, body=None):
        calls.append((method, path, timeout_ms, body))
        if path.endswith("/acquire"):
            return {"acquired": True, "last_accepted_at": 0.0, "lease_seconds": 120}
        return {"completed": True, "last_accepted_at": 1_800_000_000.0}

    monkeypatch.setattr(dispatch_lock.state_broker, "request", fake_request)
    with dispatch_lock.dispatch_admission_lock() as state:
        assert state["accepted"] is False
        state["accepted"] = True

    assert [call[1] for call in calls] == [
        "/v1/dispatch-admission/acquire",
        "/v1/dispatch-admission/complete",
    ]
    assert calls[0][0] == calls[1][0] == "POST"
    assert calls[0][3]["request_id"] == calls[1][3]["request_id"]
    assert calls[1][3]["accepted"] is True
    assert all(call[2] == 3500 for call in calls)


def test_dispatch_admission_context_releases_unaccepted_reservation(monkeypatch):
    from app import autonomous_engineering_dispatch_lock as dispatch_lock

    calls = []
    def fake_request(method, path, *, timeout_ms=3500, body=None):
        calls.append((path, body))
        return {"acquired": True} if path.endswith("/acquire") else {"completed": True}

    monkeypatch.setattr(dispatch_lock.state_broker, "request", fake_request)
    with pytest.raises(RuntimeError, match="intentional"):
        with dispatch_lock.dispatch_admission_lock():
            raise RuntimeError("intentional")
    assert calls[-1][1]["accepted"] is False


def test_dispatch_grace_window_is_shared_by_separate_requests(monkeypatch):
    state = {"accepted": False}
    calls = []
    @contextmanager
    def shared_lock():
        if state["accepted"]:
            raise RuntimeError("dispatch_admission_conflict")
        yield state

    monkeypatch.setattr(agent, "dispatch_admission_lock", shared_lock)
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True, "paused": False,
        "github": {"runs_available": True, "recent_runs": []},
        "pending_work": {"available": True, "blocked": False, "checkpoint": {"available": False}},
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8, **kwargs: (
        calls.append(command) or subprocess.CompletedProcess(command, 0, "[]", "")
    ))
    first = agent.dispatch_autonomous_engineering()
    assert first["accepted"] is True
    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()
    assert error.value.status_code == 409
    assert error.value.headers["X-Error-Code"] == "AUTONOMOUS_DISPATCH_RECENTLY_ACCEPTED"
    assert sum(command[1:3] == ["workflow", "run"] for command in calls) == 1

@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (404, "AUTONOMOUS_ENGINEERING_NOT_FOUND"),
        (405, "AUTONOMOUS_ENGINEERING_METHOD_NOT_ALLOWED"),
    ],
)
def test_unmatched_autonomous_engineering_routes_use_structured_errors(status_code, expected_code):
    async def call_next(_request):
        return JSONResponse(status_code=status_code, content={"detail": "Not Found"})

    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/autonomous-engineering/not-a-real-route")
    )
    response = asyncio.run(main_module.log_http_error_explanation(request, call_next))
    payload = json.loads(response.body)

    assert response.status_code == status_code
    assert payload["code"] == expected_code
    assert payload["retryable"] is False
    assert payload["correlation_id"]
    assert response.headers["X-Correlation-ID"] == payload["correlation_id"]
    assert response.headers["X-Error-Code"] == expected_code
    assert response.headers["X-Retryable"] == "false"


def test_unmatched_non_autonomous_routes_keep_their_existing_response():
    async def call_next(_request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    request = SimpleNamespace(url=SimpleNamespace(path="/api/tasks/not-a-real-route"))
    response = asyncio.run(main_module.log_http_error_explanation(request, call_next))

    assert response.status_code == 404
    assert json.loads(response.body) == {"detail": "Not Found"}
    assert response.headers.get("X-Error-Code") is None


def test_status_readiness_requires_every_local_dependency(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_REPOSITORY", "cognitive-kernel/Personal-AI-assistant")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model], "model_context_length": 65536, "model_capabilities": ["completion", "tools"], "model_supports_tools": True, "model_metadata_available": True, "model_tools_metadata_available": True, "model_context_supported": True, "model_selectable": True, "model_is_codex_context_variant": False
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True, "settings": {"model": "gpt-oss:20b"}
    })

    result = agent.collect_status()

    assert result["ready"] is True
    assert all(
        value for key, value in result["checks"].items()
        if key != "model_is_codex_context_variant"
    )
    assert result["checks"]["model_is_codex_context_variant"] is False
    assert result["local_only_inference"] is True
    assert result["workflow"] == "autonomous-codex-engineering.yml"
    assert result["pending_work"]["available"] is True
    assert result["checks"]["durable_state_available"] is True


def test_status_is_not_ready_when_local_model_is_missing(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": False, "models": [], "model_context_length": None, "model_capabilities": [], "model_supports_tools": False, "model_metadata_available": False
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True
    })

    result = agent.collect_status()

    assert result["ready"] is False
    assert result["checks"]["ollama_online"] is True
    assert result["checks"]["model_installed"] is False


def test_app_readiness_does_not_require_codex_in_the_app_account(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model], "model_context_length": 65536, "model_capabilities": ["completion", "tools"], "model_supports_tools": True, "model_metadata_available": True, "model_context_supported": True, "model_selectable": True, "model_is_codex_context_variant": False
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": False, "version": None})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True, "settings": {"model": "gpt-oss:20b"}
    })

    result = agent.collect_status()

    assert result["ready"] is True
    assert result["checks"]["codex_installed"] is False


def test_status_is_not_ready_when_shared_progress_storage_is_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model], "model_context_length": 65536, "model_capabilities": ["completion", "tools"], "model_supports_tools": True, "model_metadata_available": True, "model_context_supported": True
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: False)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True
    })

    result = agent.collect_status()

    assert result["ready"] is False
    assert result["checks"]["progress_storage_accessible"] is False


def test_dispatch_requires_authenticated_github_cli(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: {"ready": True, "github": {"recent_runs": [], "runs_available": True}})
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(
        agent, "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "not authenticated"),
    )

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert error.value.status_code == 503


def test_dispatch_uses_only_the_configured_workflow_and_main(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "collect_status", lambda: {"ready": True, "github": {"runs_available": True}})
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.dispatch_autonomous_engineering()

    assert result["accepted"] is True
    correlation_id = result["correlation_id"]
    assert len(correlation_id) == 32
    assert calls[0][1:] == ["auth", "status", "--hostname", "github.com"]
    assert calls[1][1:] == [
        "workflow", "run", "autonomous-codex-engineering.yml",
        "--repo", "cognitive-kernel/Personal-AI-assistant", "--ref", "main",
        "--raw-field", "correlation_id=" + correlation_id,
        "--raw-field", "manual_task=",
    ]


def test_autonomous_engineering_page_and_navigation_are_present():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    main = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    learning = (root / "app" / "ai_learning" / "static" / "index.html").read_text(encoding="utf-8")
    agent_page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    static = root / "app" / "static"
    agent_script = (static / "autonomous-engineering.js").read_text(encoding="utf-8")
    status_script = (static / "autonomous-engineering-status.js").read_text(encoding="utf-8")
    controls_script = (static / "autonomous-engineering-controls.js").read_text(encoding="utf-8")
    runs_script = (static / "autonomous-engineering-runs.js").read_text(encoding="utf-8")
    progress_script = (static / "autonomous-engineering-progress.js").read_text(encoding="utf-8")
    core_script = (static / "autonomous-engineering-core.js").read_text(encoding="utf-8")

    assert 'href="/autonomous-engineering"' in main
    assert 'href="/autonomous-engineering"' in learning
    assert 'href="/ai-learning"' in page
    assert 'href="/autonomous-engineering" aria-current="page"' in page
    assert 'id="dispatch-agent"' in page
    assert 'id="cancel-agent"' in page
    assert 'id="pause-agent"' in page
    assert 'id="resume-agent"' in page
    assert "OPENAI_API_KEY" not in page
    assert "سرویس Broker" in agent_page
    assert 'id="checkpoint-state"' in agent_page
    assert 'id="checkpoint-detail"' in agent_page
    assert 'id="manual-task-state"' in agent_page
    assert "data.pending_work" in status_script
    assert "checkpoint.verified" in status_script
    assert "function canStartManualTask()" in status_script
    assert "taskButton.disabled = !canStartManualTask()" in status_script
    assert "فقط Runner و Broker به فایل‌های محافظت‌شده دسترسی مستقیم دارند" in agent_page
    assert "حساب‌های برنامه از کانال محلی محدود استفاده می‌کنند" in agent_page
    assert "checks.github_runs_available" in status_script
    assert 'id="interval-hours"' in page
    assert 'id="execution-mode"' in page
    assert 'value="continuous"' in page
    assert 'value="continuous" selected' in page
    assert 'id="ollama-model"' in page
    assert 'id="custom-model"' in page
    assert 'id="agent-task-form"' in page
    assert 'id="manual-task"' in page
    assert 'id="send-agent-task"' in page
    assert 'id="save-agent-settings"' in page
    assert 'id="storage-state"' in page
    assert "checks.progress_storage_accessible" in status_script
    assert "gpt-oss:20b" not in page
    assert 'هیچ مدل اصلی قابل انتخابی از Ollama شناسایی نشد' in status_script
    assert "model_context_length" in status_script
    assert "model_supports_tools" in status_script
    assert "model_selectable" in status_script
    assert "نسخهٔ داخلی Context" in status_script


def test_autonomous_engineering_routes_are_registered():
    from app.main import app

    page_paths = {getattr(route, "path", None) for route in app.routes}
    api_paths = {route.path for route in agent.router.routes}

    assert "/autonomous-engineering" in page_paths
    assert "/api/autonomous-engineering/status" in api_paths
    assert "/api/autonomous-engineering/progress" in api_paths
    assert "/api/autonomous-engineering/settings" in api_paths
    assert "/api/autonomous-engineering/dispatch" in api_paths
    assert "/api/autonomous-engineering/cancel" in api_paths
    assert "/api/autonomous-engineering/pause" in api_paths
    assert "/api/autonomous-engineering/resume" in api_paths

def test_pause_sets_durable_repository_variable(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )

    result = agent.pause_autonomous_engineering()

    assert result["paused"] is True
    assert calls[0][1:3] == ["auth", "status"]
    assert calls[1][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED",
        "--body", "true", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]


def _paused_ready_snapshot(active_runs=None):
    return {
        "ready": True,
        "paused": True,
        "checks": {"durable_state_available": True},
        "pending_work": {"available": True, "blocked": False, "checkpoint": {"available": False, "verified": True}},
        "github": {"runs_available": True, "recent_runs": active_runs or []},
    }


def test_resume_uses_locked_dispatch_and_clears_pause_only_after_acceptance(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot())

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append((command, raise_os_error))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.resume_autonomous_engineering()

    assert result["accepted"] is True
    assert result["paused"] is False
    assert "در حالت زمان‌بندی‌شده این اجرا یک‌باره است" in result["message"]
    assert len(result["correlation_id"]) == 32
    workflow_call = next(call for call in calls if call[0][1:3] == ["workflow", "run"])
    assert workflow_call[0].count("--raw-field") == 3
    assert "correlation_id=" + result["correlation_id"] in workflow_call[0]
    assert "manual_task=" in workflow_call[0]
    assert "resume_once=true" in workflow_call[0]
    assert workflow_call[1] is True
    pause_update = next(call for call in calls if call[0][1:3] == ["variable", "set"])
    assert pause_update[0][2:5] == ["set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body"]
    assert pause_update[0][5] == "false"
    assert calls.index(workflow_call) < calls.index(pause_update)
    assert all("AUTONOMOUS_ENGINEERING_FAILURE_STREAK" not in call[0] for call in calls)


def test_resume_unpauses_existing_active_run_without_dispatching_duplicate(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot([{"id": 999, "status": "in_progress"}]))
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8, **kwargs: (
        calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
    ))
    result = agent.resume_autonomous_engineering()
    assert result["accepted"] is True
    assert result["dispatch_status"] == "existing_run_active"
    assert result["paused"] is False
    assert not any(command[1:3] == ["workflow", "run"] for command in calls)
    pause_update = next(command for command in calls if command[1:3] == ["variable", "set"])
    assert pause_update[3:6] == ["AUTONOMOUS_ENGINEERING_PAUSED", "--body", "false"]


def test_resume_lock_conflict_fails_before_github_or_pause_mutation(monkeypatch):
    @contextmanager
    def busy_lock():
        raise RuntimeError("dispatch_admission_conflict")
        yield {"accepted": False}

    monkeypatch.setattr(agent, "dispatch_admission_lock", busy_lock)
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot())
    monkeypatch.setattr(agent, "_run", lambda *args, **kwargs: pytest.fail("GitHub must not be called without admission"))
    with pytest.raises(HTTPException) as error:
        agent.resume_autonomous_engineering()
    assert error.value.status_code == 409
    assert error.value.headers["X-Error-Code"] == "AUTONOMOUS_DISPATCH_RECENTLY_ACCEPTED"


def test_resume_unexpected_preflight_error_fails_closed_without_pause_mutation(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: (_ for _ in ()).throw(RuntimeError("status unavailable")))
    monkeypatch.setattr(agent, "_run", lambda *args, **kwargs: pytest.fail("GitHub must not be called after failed preflight"))
    with pytest.raises(HTTPException) as error:
        agent.resume_autonomous_engineering()
    assert error.value.status_code == 503
    assert error.value.headers["X-Retryable"] == "false"


def test_resume_acceptance_suppresses_following_manual_dispatch(monkeypatch):
    state = {"accepted": False}
    @contextmanager
    def shared_lock():
        if state["accepted"]:
            raise RuntimeError("dispatch_admission_conflict")
        yield state

    monkeypatch.setattr(agent, "dispatch_admission_lock", shared_lock)
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot())
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    calls = []
    def fake_run(command, timeout=8, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(agent, "_run", fake_run)

    resumed = agent.resume_autonomous_engineering()
    assert resumed["accepted"] is True
    assert state["accepted"] is True
    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()
    assert error.value.status_code == 409
    assert sum(command[1:3] == ["workflow", "run"] for command in calls) == 1


def test_resume_dispatch_timeout_is_ambiguous_and_non_retryable(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot())
    calls = []

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append((command, raise_os_error))
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert raise_os_error is True
        return None

    monkeypatch.setattr(agent, "_run", fake_run)
    with pytest.raises(HTTPException) as error:
        agent.resume_autonomous_engineering()

    assert error.value.status_code == 502
    assert error.value.headers["X-Error-Code"] == "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN"
    assert error.value.headers["X-Retryable"] == "false"
    assert error.value.headers["X-Correlation-ID"] in error.value.detail
    assert "نامشخص" in error.value.detail
    assert "تاریخچهٔ اجراها را بازخوانی کنید" in error.value.detail
    assert calls[-1][1] is True
    assert not any(call[0][1:3] == ["variable", "set"] for call in calls)


def test_resume_dispatch_launch_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "collect_status", lambda: _paused_ready_snapshot())

    def fake_run(command, timeout=8, *, raise_os_error=False):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert raise_os_error is True
        raise FileNotFoundError("gh executable disappeared")

    monkeypatch.setattr(agent, "_run", fake_run)
    with pytest.raises(HTTPException) as error:
        agent.resume_autonomous_engineering()

    assert error.value.status_code == 503
    assert error.value.headers["X-Error-Code"] == "WORKFLOW_RESUME_DISPATCH_LAUNCH_FAILED"
    assert error.value.headers["X-Retryable"] == "true"
    assert error.value.headers["X-Correlation-ID"] in error.value.detail


def test_autonomous_recovery_uses_the_durable_policy_module():
    root = Path(__file__).resolve().parents[1]
    recovery = (root / ".github" / "workflows" / "autonomous-codex-recovery.yml").read_text(encoding="utf-8")
    policy = (root / "scripts" / "autonomous_engineering_recovery.py").read_text(encoding="utf-8")

    assert "workflow_run:" in recovery
    assert "AUTONOMOUS_ENGINEERING_PAUSED" in policy
    assert "PAUSED:" not in recovery
    assert "scripts/autonomous_engineering_recovery.py" in recovery
    assert "EXECUTION_MODE:" not in recovery
    assert "AUTONOMOUS_ENGINEERING_MODE" in policy
    assert "FAILED_RUN_ID:" in recovery
    assert "AUTONOMOUS_ENGINEERING_FAILURE_STREAK" not in policy
    assert "manual_task" in policy
    assert "retryable_failure_streak" in policy
    assert "classify_failed_run" in policy


def test_ollama_status_bypasses_environment_proxies(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured.setdefault("requests", []).append((request.full_url, timeout))
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"gpt-oss:20b","size":5368709120}]}')
            return io.BytesIO(
                b'{"details":{"family":"llama"},"model_info":{"llama.context_length":65536,"llama.block_count":32,"llama.attention.head_count_kv":8,"llama.attention.key_length":128,"llama.attention.value_length":128},'
                b'"capabilities":["completion","tools"]}'
            )

    def fake_build_opener(*handlers):
        captured["proxy_maps"] = [
            handler.proxies
            for handler in handlers
            if isinstance(handler, agent.urllib.request.ProxyHandler)
        ]
        captured["redirect_handlers"] = [
            handler
            for handler in handlers
            if isinstance(handler, agent.urllib.request.HTTPRedirectHandler)
        ]
        return FakeOpener()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setattr(agent.urllib.request, "build_opener", fake_build_opener)
    from app import hardware, ollama
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: {"models": []})
    monkeypatch.setattr(ollama, "_model_memory_estimate", lambda *args: {
        "weights_gib": 1.0, "overhead_gib": 0.5,
        "kv_cache_bytes_per_token_upper_bound": 1024,
    })
    monkeypatch.setattr(ollama, "_recommended_context_tokens", lambda *args, **kwargs: 32768)
    monkeypatch.setattr(hardware, "detect_hardware", lambda: {})

    result = agent._ollama_status("gpt-oss:20b")

    assert captured["proxy_maps"] == [{}]
    assert len(captured["redirect_handlers"]) == 1
    assert captured["redirect_handlers"][0].redirect_request(
        None, None, 302, "Found", {}, "https://redirect.example/api/tags"
    ) is None
    assert captured["requests"] == [
        ("http://127.0.0.1:11434/api/tags", 3),
        ("http://127.0.0.1:11434/api/show", 5),
    ]
    assert result["online"] is True
    assert result["model_installed"] is True
    assert result["model_context_length"] == 65536
    assert result["model_recommended_context_length"] == 32768
    assert result["model_supports_tools"] is True
    assert result["model_context_supported"] is True


def test_standalone_ollama_tool_probe_uses_local_endpoint_and_structured_tool_call(monkeypatch):
    from scripts import verify_ollama_tools

    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/ps"):
                return io.BytesIO(json.dumps({"models": []}).encode("utf-8"))
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return io.BytesIO(json.dumps({
                "message": {"tool_calls": [{
                    "function": {"name": "autonomous_engineering_capability_probe", "arguments": {}}
                }]}
            }).encode("utf-8"))

    monkeypatch.setattr(verify_ollama_tools.urllib.request, "build_opener", lambda *args: FakeOpener())

    assert verify_ollama_tools.verify_tool_calling("gpt-oss:20b") is True
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout"] == 45
    assert captured["payload"]["think"] is False
    assert captured["payload"]["keep_alive"] == "0"
    assert captured["payload"]["tools"][0]["function"]["name"] == "autonomous_engineering_capability_probe"


def test_ollama_status_probes_tool_calling_when_capabilities_metadata_is_missing(monkeypatch):
    from app import ollama

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(json.dumps({
                    "models": [{"name": "gpt-oss:20b", "size": 5 * 1024**3, "digest": "sha256:model"}]
                }).encode("utf-8"))
            if request.full_url.endswith("/api/show"):
                return io.BytesIO(json.dumps({
                    "details": {"family": "incorrect-family"},
                    "model_info": {
                        "general.architecture": "gptoss",
                        "gptoss.context_length": 131072,
                        "gptoss.block_count": 24,
                        "gptoss.attention.head_count_kv": 8,
                        "gptoss.attention.key_length": 64,
                        "gptoss.attention.value_length": 64,
                    },
                }).encode("utf-8"))
            if request.full_url.endswith("/api/chat"):
                return io.BytesIO(json.dumps({
                    "message": {"tool_calls": [{
                        "function": {"name": "autonomous_engineering_capability_probe", "arguments": {}}
                    }]}
                }).encode("utf-8"))
            raise AssertionError(request.full_url)

    agent._services._TOOL_PROBE_CACHE.clear()
    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: FakeOpener())
    from app import hardware
    monkeypatch.setattr(ollama, "_model_memory_estimate", lambda *args: {
        "weights_gib": 1.0, "overhead_gib": 0.5,
        "kv_cache_bytes_per_token_upper_bound": 1024,
    })
    monkeypatch.setattr(ollama, "_recommended_context_tokens", lambda *args, **kwargs: 32768)
    monkeypatch.setattr(hardware, "detect_hardware", lambda: {})
    probe_calls = []
    def fake_probe_request(path, payload=None, timeout=None):
        probe_calls.append((path, payload, timeout))
        if path == "/api/ps":
            return {"models": []}
        return {"message": {"tool_calls": [{
            "function": {"name": "autonomous_engineering_capability_probe", "arguments": {}}
        }]}}
    monkeypatch.setattr(ollama, "_request", fake_probe_request)

    result = agent._ollama_status("gpt-oss:20b")

    assert result["model_context_length"] == 131072
    assert result["model_supports_tools"] is True
    assert result["model_tools_metadata_available"] is False
    assert result["model_tools_verification_method"] == "live_tool_call_probe"
    assert [call[0] for call in probe_calls] == ["/api/ps", "/api/ps", "/api/chat"]
    assert probe_calls[-1][2] == 45
    assert probe_calls[-1][1]["think"] is False
    assert probe_calls[-1][1]["keep_alive"] == "0"


def test_ollama_status_lists_installed_models_without_selecting_one(monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/api/tags"
            return io.BytesIO(b'{"models":[{"name":"z-model:latest"},{"name":"a-model:7b"}]}')

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: FakeOpener())

    result = agent._ollama_status("")

    assert result["online"] is True
    assert result["models"] == ["a-model:7b", "z-model:latest"]
    assert result["model"] == ""
    assert result["model_installed"] is False
    assert result["model_metadata_error"] == "No Ollama model is saved in repository settings."


def test_status_is_not_ready_when_saved_model_is_a_generated_context_variant(monkeypatch):
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "settings": {"model": "base-model:14b-codex-ctx65536"},
        "authenticated": True,
        "recent_runs": [],
        "runs_available": True,
    })
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True,
        "model": model,
        "model_installed": True,
        "model_selectable": False,
        "model_is_codex_context_variant": True,
        "models": ["base-model:14b"],
        "model_context_length": None,
        "model_capabilities": [],
        "model_supports_tools": False,
        "model_metadata_available": False,
        "model_context_supported": False,
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)

    result = agent.collect_status()

    assert result["ready"] is False
    assert result["checks"]["model_installed"] is True
    assert result["checks"]["model_selectable"] is False
    assert result["checks"]["model_is_codex_context_variant"] is True
    assert result["model_is_codex_context_variant"] is True


def test_ollama_status_hides_generated_codex_context_variants_and_rejects_saved_alias(monkeypatch):
    calls = []

    class FakeOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            assert request.full_url == "http://127.0.0.1:11434/api/tags"
            return io.BytesIO(
                b'{"models":[{"name":"base-model:14b"},{"name":"base-model:14b-codex-ctx65536"}]}'
            )

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *args: FakeOpener())

    result = agent._ollama_status("base-model:14b-codex-ctx65536")

    assert calls == ["http://127.0.0.1:11434/api/tags"]
    assert result["online"] is True
    assert result["model_installed"] is True
    assert result["model_selectable"] is False
    assert result["model_is_codex_context_variant"] is True
    assert result["models"] == ["base-model:14b", "base-model:14b-codex-ctx65536"]
    assert result["selectable_models"] == ["base-model:14b"]
    assert "original Ollama model" in result["model_metadata_error"]


def test_save_settings_rejects_generated_codex_context_variant_before_local_or_github_access(monkeypatch):
    def unexpected_model_lookup(model):
        raise AssertionError("Generated context variants must be rejected before Ollama access.")

    monkeypatch.setattr(agent, "_ollama_status", unexpected_model_lookup)
    monkeypatch.setattr(agent.shutil, "which", lambda _: None)

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(
                model="base-model:14b-codex-ctx65536",
                interval_hours=4,
                execution_mode="continuous",
            )
        )

    assert error.value.status_code == 422
    assert "مدل اصلی Ollama" in error.value.detail


def test_github_snapshot_fails_closed_when_run_list_is_unavailable(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    calls = []

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "permission denied")

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent._github_snapshot()

    assert len(calls) == 3
    assert result["authenticated"] is True
    assert result["runs_available"] is False
    assert result["recent_runs"] == []


def test_dispatch_fails_closed_when_run_list_is_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": False,
        "github": {"runs_available": False},
    })

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert error.value.status_code == 503



@pytest.mark.parametrize(
    ("local_model", "expected_status"),
    [
        ({"online": True, "model_installed": False}, 422),
        ({"online": True, "model_installed": True, "model_metadata_available": False}, 422),
        ({"online": True, "model_installed": True, "model_metadata_available": True, "model_context_supported": True, "model_supports_tools": False}, 422),
        ({"online": True, "model_installed": True, "model_metadata_available": True, "model_context_supported": False, "model_supports_tools": True}, 422),
    ],
)
def test_save_settings_rejects_uninstalled_or_unsupported_models_before_github_writes(
    monkeypatch, local_model, expected_status
):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "model": model,
        "online": local_model.get("online", False),
        "model_installed": local_model.get("model_installed", False),
        "model_metadata_available": local_model.get("model_metadata_available", False),
        "model_context_supported": local_model.get("model_context_supported", False),
        "model_supports_tools": local_model.get("model_supports_tools", False),
    })
    calls = []
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model="qwen3:14b", interval_hours=4, execution_mode="continuous")
        )

    assert error.value.status_code == expected_status
    assert calls == []


def test_save_settings_updates_model_and_hourly_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True,
        "model_metadata_available": True, "model_context_supported": True, "model_supports_tools": True,
    })

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["variable", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent.save_autonomous_engineering_settings(
        agent.EngineeringSettings(model="qwen3:14b", interval_hours=4, execution_mode="continuous")
    )

    assert result["saved"] is True
    assert result["model"] == "qwen3:14b"
    assert result["interval_hours"] == 4
    assert result["execution_mode"] == "continuous"
    assert calls[0][1:4] == ["auth", "status", "--hostname"]
    assert calls[1][1:] == [
        "variable", "list", "--repo", "cognitive-kernel/Personal-AI-assistant",
        "--json", "name,value",
    ]
    assert calls[2][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_MODEL",
        "--body", "qwen3:14b", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]
    assert calls[3][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS",
        "--body", "4", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]
    assert calls[4][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_MODE",
        "--body", "continuous", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]


def test_save_settings_rolls_back_previous_repository_values_after_partial_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True,
        "model_metadata_available": True, "model_context_supported": True, "model_supports_tools": True,
    })

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["variable", "list"]:
            state = [
                {"name": "AUTONOMOUS_ENGINEERING_MODEL", "value": "old-model:7b"},
                {"name": "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS", "value": "24"},
                {"name": "AUTONOMOUS_ENGINEERING_MODE", "value": "scheduled"},
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
        if command[1:3] == ["variable", "set"] and command[3] == "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS":
            return subprocess.CompletedProcess(command, 1, "", "permission denied")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model="qwen3:14b", interval_hours=4, execution_mode="continuous")
        )

    assert error.value.status_code == 502
    assert "all earlier changes were rolled back" in error.value.detail
    assert calls[-1][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_MODEL",
        "--body", "old-model:7b", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]


@pytest.mark.parametrize(
    ("model", "interval_hours"),
    [("bad model name", 1), ("qwen3:14b", 5), ("../unsafe", 2)],
)
def test_save_settings_rejects_invalid_values(monkeypatch, model, interval_hours):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model=model, interval_hours=interval_hours)
        )

    assert error.value.status_code == 422


def test_save_settings_rolls_back_a_write_that_failed_after_remote_acceptance(monkeypatch):
    calls = []
    state = {
        "AUTONOMOUS_ENGINEERING_MODEL": "old-model:7b",
        "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS": "24",
        "AUTONOMOUS_ENGINEERING_MODE": "scheduled",
    }
    interval_update_attempted = False
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True,
        "model_metadata_available": True, "model_context_supported": True, "model_supports_tools": True,
    })

    def fake_run(command, timeout=8, *, raise_os_error=False):
        nonlocal interval_update_attempted
        calls.append(command)
        if command[1:3] == ["variable", "list"]:
            payload = [{"name": name, "value": value} for name, value in state.items()]
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[1:3] == ["variable", "set"]:
            name, value = command[3], command[5]
            if name == "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS" and value == "4" and not interval_update_attempted:
                interval_update_attempted = True
                state[name] = value
                return subprocess.CompletedProcess(command, 1, "", "connection lost after server accepted update")
            state[name] = value
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model="qwen3:14b", interval_hours=4, execution_mode="continuous")
        )

    assert error.value.status_code == 502
    assert "all earlier changes were rolled back" in error.value.detail
    assert state == {
        "AUTONOMOUS_ENGINEERING_MODEL": "old-model:7b",
        "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS": "24",
        "AUTONOMOUS_ENGINEERING_MODE": "scheduled",
    }


def test_collect_status_never_uses_process_environment_as_model_fallback(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_MODEL", "gpt-oss:20b")
    observed = {}
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {"settings": {}, "authenticated": False, "recent_runs": [], "runs_available": False})
    def fake_ollama_status(model):
        observed["model"] = model
        return {
            "online": False, "model": model, "model_installed": False, "models": [],
            "model_context_length": None, "model_capabilities": [],
            "model_supports_tools": False, "model_metadata_available": False,
        }

    monkeypatch.setattr(agent, "_ollama_status", fake_ollama_status)
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": False, "version": None})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": False, "process_detected": False})
    monkeypatch.setattr(agent, "storage_accessible", lambda: False)

    result = agent.collect_status()

    assert observed["model"] == ""
    assert result["model"] == ""


def test_github_snapshot_does_not_invent_a_model_when_no_repository_variable_is_saved(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_MODEL", raising=False)
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if command[1:3] == ["variable", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent._github_snapshot()
    assert result["settings"]["model"] == ""


def test_autonomous_workflow_requires_saved_model_without_a_hardcoded_fallback():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    assert "vars.AUTONOMOUS_ENGINEERING_MODEL || ''" in workflow
    assert "No model is saved in repository variable AUTONOMOUS_ENGINEERING_MODEL" in workflow
    assert "vars.AUTONOMOUS_ENGINEERING_MODEL || 'gpt-oss:20b'" not in workflow
    for relative_path in (
        "scripts/check_autonomous_agent.ps1",
        "scripts/check_autonomous_runner.ps1",
        "scripts/provision_autonomous_runner.ps1",
    ):
        script = (root / relative_path).read_text(encoding="utf-8")
        assert "gpt-oss:20b" not in script
    assert "variable get AUTONOMOUS_ENGINEERING_MODEL" in (
        root / "scripts" / "provision_autonomous_runner.ps1"
    ).read_text(encoding="utf-8")
    dashboard = (root / "app" / "static" / "autonomous-engineering-status.js").read_text(encoding="utf-8")
    assert "ابتدا مدل نصب‌شده را انتخاب کنید" in dashboard
    assert "installed.includes(selectedModel)" in dashboard
    assert "هنوز مدلی ذخیره نشده است" in dashboard
    assert "مدل ذخیره‌شده روی این سیستم نصب نیست" in dashboard


def test_github_snapshot_reads_saved_model_and_frequency(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if command[1:3] == ["variable", "list"]:
            return subprocess.CompletedProcess(
                command, 0,
                '[{"name":"AUTONOMOUS_ENGINEERING_MODEL","value":"qwen3:14b"},'
                '{"name":"AUTONOMOUS_ENGINEERING_INTERVAL_HOURS","value":"12"},'
                '{"name":"AUTONOMOUS_ENGINEERING_MODE","value":"continuous"}]',
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent._github_snapshot()

    assert result["settings"] == {"model": "qwen3:14b", "interval_hours": 12, "execution_mode": "continuous", "paused": False}



def test_progress_reader_uses_scorecard_baseline_and_date_filtered_snapshots(monkeypatch):
    from app import autonomous_engineering_progress as progress
    from app import autonomous_engineering_state_broker as broker

    snapshot = {
        "current": None,
        "baseline": {
            "date": "2026-11-01",
            "engineering_percent": 60.47,
            "overall_percent": 57.45,
            "sha": "a" * 40,
            "source": "scorecard-baseline",
        },
        "history": [{
            "type": "project_snapshot",
            "date": "2026-11-02",
            "recorded_at": "2026-11-02T10:00:00+00:00",
            "engineering_percent": 61.0,
            "overall_percent": 58.0,
            "sha": "b" * 40,
        }],
        "events": [],
        "storage_configured": True,
    }
    monkeypatch.setattr(broker, "progress", lambda: snapshot)

    result = progress.get_progress(
        period="custom", start_date="2026-11-01", end_date="2026-11-02"
    )

    assert [item["date"] for item in result["history"]] == ["2026-11-01", "2026-11-02"]
    assert result["history"][0]["engineering_percent"] == 60.47
    assert result["history"][0]["overall_percent"] == 57.45
    assert result["history"][1]["engineering_percent"] == 61.0

def test_record_phase_preserves_plan_percentage_for_same_run(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as writer

    storage = tmp_path / "telemetry"
    storage.mkdir()
    (storage / "current.json").write_text(json.dumps({
        "run_id": "200",
        "run_attempt": "1",
        "plan_completed": 2,
        "plan_total": 4,
        "plan_percent": 50,
    }), encoding="utf-8")
    monkeypatch.setattr(writer, "store_dir", lambda: storage)

    assert writer.record_phase("200", "1", "tests", "آزمون‌ها", "در حال اجرای آزمون", 70) is True

    current = json.loads((storage / "current.json").read_text(encoding="utf-8"))
    assert current["phase"] == "tests"
    assert current["percent"] == 70
    assert current["plan_completed"] == 2
    assert current["plan_total"] == 4
    assert current["plan_percent"] == 50


def test_scorecard_snapshot_fails_when_scorecard_is_missing(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as writer

    monkeypatch.setattr(writer, "SCORECARD", tmp_path / "missing-scorecard.md")
    monkeypatch.setattr(writer, "store_dir", lambda: tmp_path / "telemetry")

    assert writer.snapshot_project("202", "1") is False


def test_record_phase_fails_when_current_progress_cannot_be_written(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as writer

    monkeypatch.setattr(writer, "store_dir", lambda: tmp_path / "unavailable")
    monkeypatch.setattr(writer, "persist", lambda record: False)

    assert writer.record_phase("201", "1", "preparation", "شروع", "آغاز", 0) is False


def test_scorecard_snapshots_are_recorded_only_when_scorecard_changes(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as writer

    scorecard = tmp_path / "ACCEPTANCE_SCORECARD.md"
    storage = tmp_path / "telemetry"
    scorecard.write_text(
        "**Snapshot:** 2026-11-01\n"
        "**Scored baseline:** main at " + "a" * 40 + "\n"
        "- **Engineering completion:** score = 60.47%\n"
        "- **Overall acceptance progress:** score = 57.45%\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(writer, "SCORECARD", scorecard)
    monkeypatch.setattr(writer, "store_dir", lambda: storage)

    assert writer.snapshot_project("100", "1") is True
    assert writer.snapshot_project("101", "1") is True

    history = storage / "history.jsonl"
    state = storage / "scorecard-state.json"
    assert state.is_file()
    baseline = json.loads((storage / "scorecard-baseline.json").read_text(encoding="utf-8"))
    assert baseline["date"] == "2026-11-01"
    assert baseline["engineering_percent"] == 60.47
    assert not history.exists()

    scorecard.write_text(
        "**Snapshot:** 2026-11-02\n"
        "**Scored baseline:** main at " + "b" * 40 + "\n"
        "- **Engineering completion:** score = 60.47%\n"
        "- **Overall acceptance progress:** score = 57.45%\n",
        encoding="utf-8",
    )
    assert writer.snapshot_project("102", "1") is True
    assert not history.exists()

    scorecard.write_text(
        "**Snapshot:** 2026-11-03\n"
        "**Scored baseline:** main at " + "c" * 40 + "\n"
        "- **Engineering completion:** score = 61.00%\n"
        "- **Overall acceptance progress:** score = 58.00%\n",
        encoding="utf-8",
    )
    assert writer.snapshot_project("103", "1") is True
    assert writer.snapshot_project("104", "1") is True

    records = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["type"] == "project_snapshot"
    assert records[0]["scorecard_snapshot_date"] == "2026-11-03"
    assert records[0]["engineering_percent"] == 61.0
    assert records[0]["overall_percent"] == 58.0
    assert records[0]["sha"] == "c" * 40


def test_progress_reader_rejects_invalid_custom_date_range():
    from app.autonomous_engineering_progress import get_progress

    result = get_progress(period="custom", start_date="2026-11-03", end_date="2026-11-01")

    assert result["available"] is False
    assert result["error"] == "بازهٔ تاریخ معتبر نیست."


def test_autonomous_engineering_progress_route_and_compact_dashboard_controls():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    script = (root / "app" / "static" / "autonomous-engineering-progress.js").read_text(encoding="utf-8")
    controls = (root / "app" / "static" / "autonomous-engineering-controls.js").read_text(encoding="utf-8")
    orchestrator = (root / "app" / "static" / "autonomous-engineering.js").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    operations = (root / "docs" / "operations" / "CODEX_AUTONOMOUS_ENGINEERING.md").read_text(encoding="utf-8")
    master_prompt = (root / "docs" / "MASTER_ENGINEERING_PROMPT.md").read_text(encoding="utf-8")
    runner_check = (root / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    acl_script = (root / "scripts" / "configure_autonomous_engineering_progress.ps1").read_text(encoding="utf-8")
    api_paths = {route.path for route in agent.router.routes}

    assert "/api/autonomous-engineering/progress" in api_paths
    assert 'id="cycle-progress"' in page
    assert 'id="progress-chart"' in page
    assert 'id="progress-period"' in page
    assert 'id="progress-start"' in page
    assert 'id="progress-end"' in page
    assert 'id="progress-events"' in page
    assert "current.plan_percent" in script
    assert 'id="dispatch-agent"' in page
    assert "اجرای یک چرخهٔ خودکار از صف مهندسی" in page
    assert 'dispatchButton.addEventListener("click"' in controls
    assert 'request("dispatch", {method: "POST"})' in controls
    assert "PR را برای بازبینی مالک باز می‌گذارد" in page
    assert "هرگز آن را خودکار ادغام نمی‌کند" in page
    assert "The controller never calls GitHub's PR merge API." in operations
    assert "The trusted controller never merges PRs" in operations
    assert "The trusted controller may merge only" not in operations
    assert "Issue #290 explicitly prohibits automatic PR merge" in master_prompt
    assert "must never call the PR merge API" in master_prompt
    assert "pre-authorized by the repository owner for autonomous merge" not in master_prompt
    assert "در main ادغام می‌کند" not in page
    assert "یک Pull Request پیش‌نویس ساخته می‌شود" not in page
    assert "gh auth login --web" in page
    assert "Codex توکن GitHub را دریافت نمی‌کند" in page
    assert "پیشرفت گام‌های برنامهٔ عامل" in page
    assert "Initialize live progress before prerequisite checks" in workflow
    assert "Preserve and stop checkpoint watcher from an interrupted run" not in workflow
    assert "scripts/autonomous_engineering_checkpoint.py" in workflow
    assert workflow.index("Snapshot accepted scorecard from trusted main") < workflow.index("Execute one isolated task, validate, and publish for owner review")
    assert workflow.count("snapshot --run-id") == 1
    progress_writer = (root / "scripts" / "autonomous_engineering_progress.py").read_text(encoding="utf-8")
    codex_config = (root / "scripts" / "autonomous_engineering_codex_config.py").read_text(encoding="utf-8")
    cycle_controller = (root / "scripts" / "autonomous_engineering_cycle.py").read_text(encoding="utf-8")
    pr_lifecycle = (root / "scripts" / "autonomous_engineering_pull_requests.py").read_text(encoding="utf-8")
    tests_workflow = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    browser_workflow = (root / ".github" / "workflows" / "browser-e2e.yml").read_text(encoding="utf-8")
    assert 'model_provider="ollama-local"' in codex_config
    assert "model_providers.ollama-local.base_url" in codex_config
    assert "model_providers.ollama." not in codex_config
    # The real task prompt must pin the same safe exec_command schema used by
    # the successful isolated Codex smoke; otherwise Ollama models may emit
    # an invalid justification/sandbox_permissions combination.
    assert "For EVERY Codex exec_command tool call" in workflow
    assert "For EVERY Codex exec_command tool call on Windows" in workflow
    assert "set `shell`" in workflow and r"C:\Windows\System32\cmd.exe" in workflow
    assert "set `login` to `false`" in workflow
    assert "Do not use the default PowerShell shell" in workflow
    assert "git status --short --branch && git rev-parse HEAD" in workflow
    assert "yield_time_ms to the" in workflow and "integer 10000" in workflow
    assert 'sandbox_permissions to "use_default"' in workflow
    assert "Omit justification, prefix_rule," in workflow
    assert "additional_permissions entirely" in workflow
    assert 'Never use "require_escalated"' in workflow
    assert "scripts/autonomous_engineering_cycle.py run" in workflow
    assert "contents: write" in workflow and "pull-requests: write" in workflow and "actions: write" in workflow
    assert "publish_pull_request" in cycle_controller and "dispatch_and_wait_all" in cycle_controller
    assert "save_pending_pr" in cycle_controller
    assert "complete_merged_pr" in cycle_controller
    assert "clear_pending_pr" in cycle_controller
    assert 'api(token, repo, "PUT", f"pulls/{number}/merge"' not in pr_lifecycle
    assert "awaiting owner review" in cycle_controller
    assert '"draft": False' in pr_lifecycle
    assert "complete_pending_pr" in cycle_controller
    assert "AUTONOMOUS_ENGINEERING_PAUSED" in workflow
    assert "AUTONOMOUS_ENGINEERING_INTERVAL_HOURS" in workflow
    assert "types: [closed]" not in workflow
    assert "TASK_COMPLETE" in cycle_controller and "QUEUE_EMPTY" in cycle_controller
    assert "open-draft-pr:" not in workflow and "  validate:" not in workflow
    assert "pytest" in cycle_controller and "tests.yml" in pr_lifecycle
    assert "workflow_dispatch:" in tests_workflow and "workflow_dispatch:" in browser_workflow
    assert "pyproject.toml" not in workflow
    assert "WriteAllText" in runner_check
    assert "gh auth login" not in runner_check
    assert "WindowsBuiltInRole]::Administrator" in runner_check
    assert "non-administrator service account" in workflow
    assert "workflow_dispatch:" in workflow
    assert "Another autonomous run is already active or queued" in workflow
    assert "push:" in workflow and 'branches: [main]' in workflow
    assert 'if [[ "$EVENT_NAME" == "push" ]]' in workflow
    assert '"$EXECUTION_MODE" == "continuous"' in workflow
    assert 'echo "run_now=true"' in workflow
    assert "manual_task:" in workflow
    assert "inputs.manual_task" in workflow
    assert "pending-manual-task.txt" in workflow
    assert "function Get-LiveRepositoryVariable" in workflow
    assert "/actions/variables/$Name" in workflow
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_PAUSED"' in workflow
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_MODE"' in workflow
    assert "[string]$RunnerAccount" in acl_script
    assert "AppAccount" not in acl_script
    assert "ensure_autonomous_state_broker.ps1" in acl_script
    assert "Get-AutonomousWindowsAccountSid -AccountName $service.StartName" in acl_script
    assert "Set-AutonomousDirectoryTreeAcl" not in acl_script
    assert "PersonalAIAssistantStateBroker" in runner_check
    assert "core.startPolling({" in orchestrator
    assert "intervalMs: 5000" in orchestrator
    assert "refresh: () => progress.refresh()" in orchestrator
    assert "createElementNS" in script


def test_live_progress_uses_actual_github_validation_step(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_RUN_PROGRESS_CACHE", {"run_id": None, "at": 0.0, "value": None})
    jobs = {
        "jobs": [{
            "name": "codex",
            "status": "in_progress",
            "steps": [
                {"name": "Checkout", "status": "completed"},
                {"name": "Apply patch", "status": "completed"},
                {"name": "Install dependencies", "status": "in_progress"},
                {"name": "Run tests", "status": "queued"},
            ],
        }]
    }
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: subprocess.CompletedProcess(command, 0, json.dumps(jobs), ""),
    )

    result = agent._enrich_progress_from_github({
        "run_id": "12345", "percent": 90, "status": "running",
        "phase": "packaging", "activity": "handoff",
    })

    assert result["phase"] == "packaging"
    assert result["percent"] == 90
    assert result["status"] == "running"
    assert result["activity"] == "Install dependencies"


def test_live_progress_marks_all_successful_workflow_jobs_complete(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_RUN_PROGRESS_CACHE", {"run_id": None, "at": 0.0, "value": None})
    jobs = {
        "jobs": [
            {"name": "codex", "status": "completed", "conclusion": "success", "steps": []},
        ]
    }
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: subprocess.CompletedProcess(command, 0, json.dumps(jobs), ""),
    )

    result = agent._enrich_progress_from_github({"run_id": "12346", "percent": 98})

    assert result["status"] == "completed"
    assert result["percent"] == 100
    assert result["phase"] == "complete"


def test_codex_progress_plan_reports_completed_steps_without_raw_output(monkeypatch, tmp_path):
    from scripts import autonomous_engineering_progress as writer

    plan = tmp_path / ".agent-progress.json"
    plan.write_text(json.dumps({
        "schema_version": 1,
        "plan": [
            {"id": "a", "title": "Inspect backend", "status": "completed"},
            {"id": "b", "title": "Implement fix", "status": "in_progress"},
            {"id": "c", "title": "Run regression tests", "status": "pending"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(writer, "PLAN_FILE", plan)

    completed, total, active = writer.plan_state()
    activity = writer.codex_activity({
        "type": "item.started",
        "item": {"type": "command_execution", "command": "python -m pytest --secret-value hidden"},
    })

    assert (completed, total, active) == (1, 3, "Implement fix")
    assert writer.plan_percentage(completed, total) == 33.33
    assert writer.plan_percentage(2, 3) == 66.67
    assert writer.plan_percentage(0, 0) is None
    assert activity == "در حال اجرای ابزار python"
    assert "hidden" not in activity


def test_live_progress_reports_queued_github_validation_phase(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_RUN_PROGRESS_CACHE", {"run_id": None, "at": 0.0, "value": None})
    jobs = {
        "jobs": [
            {"name": "codex", "status": "queued", "conclusion": None, "steps": []},
        ]
    }
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: subprocess.CompletedProcess(command, 0, json.dumps(jobs), ""),
    )

    result = agent._enrich_progress_from_github({"run_id": "12347", "percent": 90})

    assert result["phase"] == "queued"
    assert result["phase_label"] == "در انتظار Runner خودمیزبان"
    assert result["status"] == "queued"


def test_dispatch_forwards_manual_task_as_a_single_untrusted_field(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True, "github": {"runs_available": True, "recent_runs": []}
    })
    monkeypatch.setattr(agent, "pending_work_status", lambda: {
        "available": True, "blocked": False, "markers": {},
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:2] == ["api"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    task = "بررسی Issue #52؛ عبارت ; gh secret list نباید اجرا شود"

    result = agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task=task))

    assert result["accepted"] is True
    assert len(result["correlation_id"]) == 32
    assert calls[-1][-2:] == ["--raw-field", "manual_task=" + task]
    assert calls[-1][-4] == "--raw-field"
    assert calls[-1][-3] == "correlation_id=" + result["correlation_id"]
    assert calls[-1].count("--field") == 0


def test_manual_task_rejects_null_byte_and_excessive_length():
    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="\x00"))
    assert error.value.status_code == 422

    with pytest.raises(ValueError):
        agent.ManualTaskRequest(task="x" * 2001)


def test_autonomous_cycle_requires_one_exact_completion_marker(tmp_path):
    from scripts import autonomous_engineering_cycle as cycle

    report = tmp_path / "report.md"
    report.write_text("Task evidence\nTASK_COMPLETE\n", encoding="utf-8")
    assert cycle.task_marker(report) == "TASK_COMPLETE"

    report.write_text("Task evidence\n- `task_complete`.\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="final non-empty report line"):
        cycle.task_marker(report)

    report.write_text("- Example quote: TASK_COMPLETE\nTASK_COMPLETE\n", encoding="utf-8")
    assert cycle.task_marker(report) == "TASK_COMPLETE"

    report.write_text("TASK_COMPLETE\nAdditional prose after the marker\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="final non-empty report line"):
        cycle.task_marker(report)


def test_autonomous_cycle_rejects_protected_and_non_allowlisted_paths():
    from scripts import autonomous_engineering_cycle as cycle

    cycle.validate_paths(["app/example.py", "tests/test_example.py"])
    for path in (
        ".github/workflows/autonomous-codex-engineering.yml",
        "scripts/autonomous_engineering_cycle.py",
        ".env",
        "../outside.py",
    ):
        with pytest.raises(RuntimeError, match="protected or non-allowlisted"):
            cycle.validate_paths([path])

def test_live_progress_does_not_freeze_legacy_awaiting_review_state(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_RUN_PROGRESS_CACHE", {"run_id": None, "at": 0.0, "value": None})
    jobs = {"jobs": [{
        "name": "codex", "status": "in_progress",
        "steps": [{"name": "Execute isolated engineering task", "status": "in_progress"}],
    }]}
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: subprocess.CompletedProcess(command, 0, json.dumps(jobs), ""),
    )
    current = {
        "run_id": "41400041710", "status": "awaiting_review",
        "phase": "coding", "phase_label": "اجرای چرخهٔ مهندسی",
        "activity": "در حال اجرا", "percent": 60,
    }

    result = agent._enrich_progress_from_github(current)

    assert result["status"] == "running"
    assert result["activity"] == "Execute isolated engineering task"




def test_status_distinguishes_prerequisite_readiness_from_paused_dispatch(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model], "model_context_length": 65536, "model_capabilities": ["completion", "tools"], "model_supports_tools": True, "model_metadata_available": True, "model_context_supported": True, "model_selectable": True, "model_is_codex_context_variant": False
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "runs_available": True,
        "recent_runs": [], "settings": {"model": "gpt-oss:20b", "paused": True},
    })

    result = agent.collect_status()

    assert result["ready"] is True
    assert result["paused"] is True
    assert result["can_dispatch"] is False


def test_dispatch_rejects_safe_paused_state(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True, "paused": True,
        "github": {"runs_available": True, "recent_runs": []},
    })

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task=""))

    assert error.value.status_code == 409
    assert "توقف ایمن" in error.value.detail


def test_github_snapshot_inspects_same_run_window_as_schedule_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    runs = [
        {
            "databaseId": index + 1,
            "status": "completed",
            "conclusion": "success",
            "createdAt": "2026-11-06T00:00:00Z",
            "url": f"https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/{index + 1}",
            "headBranch": "main",
        }
        for index in range(29)
    ]
    runs.append({
        "databaseId": 999,
        "status": "queued",
        "conclusion": None,
        "createdAt": "2026-11-05T00:00:00Z",
        "url": "https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/999",
        "headBranch": "main",
    })

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(runs), "")
        if command[1:3] == ["variable", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent._github_snapshot()

    run_command = next(command for command in calls if command[1:3] == ["run", "list"])
    assert run_command[run_command.index("--limit") + 1] == "30"
    assert len(result["recent_runs"]) == 6
    assert result["recent_runs"][-1]["status"] == "queued"


def test_cancel_targets_only_the_run_id_confirmed_by_the_ui_snapshot(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda gh, path, timeout=15: {
        "id": 999,
        "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "in_progress",
    } if path == "actions/runs/999" else pytest.fail(f"Unexpected API path: {path}"))

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "jobs": [{"name": "Codex", "status": "in_progress"}]
            }), "")
        if command[1:3] == ["run", "cancel"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=999))
    assert result["run_id"] == 999
    assert calls[-1][1:3] == ["run", "cancel"]
    assert calls[-1][3] == "999"


def test_cancel_uses_same_local_mutation_lock_as_dispatch_and_resume(monkeypatch):
    class BusyLock:
        def acquire(self, blocking=False):
            return False
        def release(self):
            pytest.fail("A lock not acquired must not be released")

    monkeypatch.setattr(agent, "_DISPATCH_REQUEST_LOCK", BusyLock())
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: pytest.fail("GitHub must not be queried while mutation lock is busy"))
    with pytest.raises(HTTPException) as error:
        agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=1004))
    assert error.value.status_code == 409
    assert error.value.headers["X-Error-Code"] == "AUTONOMOUS_CANCEL_IN_PROGRESS"


def test_cancel_refuses_selected_run_without_active_codex_job(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda gh, path, timeout=15: {
        "id": 1001,
        "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "in_progress",
    })
    def fake_run(command, timeout=8, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "jobs": [{"name": "Codex", "status": "completed"}]
        }), "")
    monkeypatch.setattr(agent, "_run", fake_run)
    with pytest.raises(HTTPException) as error:
        agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=1001))
    assert error.value.status_code == 409
    assert not any(command[1:3] == ["run", "cancel"] for command in calls)


def test_resume_preflight_failure_keeps_pause_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    snapshot = _paused_ready_snapshot()
    snapshot["ready"] = False
    monkeypatch.setattr(agent, "collect_status", lambda: snapshot)
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8, **kwargs: (
        calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
    ))
    with pytest.raises(HTTPException) as error:
        agent.resume_autonomous_engineering()
    assert error.value.status_code == 409
    assert calls == []


def test_cancel_fails_closed_when_selected_run_has_multiple_codex_jobs(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 1002,
        "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "in_progress",
    })
    def fake_run(command, timeout=8, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "jobs": [
                {"name": "Codex", "status": "in_progress"},
                {"name": "Codex", "status": "queued"},
            ]
        }), "")
    monkeypatch.setattr(agent, "_run", fake_run)
    with pytest.raises(HTTPException) as error:
        agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=1002))
    assert error.value.status_code == 502
    assert not any(command[1:3] == ["run", "cancel"] for command in calls)


def test_cancel_can_cancel_the_exact_queued_snapshot_run(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 1003,
        "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "queued",
    })
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8, **kwargs: (
        calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
    ))
    result = agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=1003))
    assert result["run_id"] == 1003
    assert calls[-1][1:4] == ["run", "cancel", "1003"]


def test_cancel_rejects_snapshot_run_that_is_no_longer_active(monkeypatch):
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 999,
        "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "completed",
    })
    monkeypatch.setattr(agent, "_run", lambda *args, **kwargs: pytest.fail("A completed run must not be cancelled"))
    with pytest.raises(HTTPException) as error:
        agent.cancel_autonomous_engineering(agent.CancelRequest(run_id=999))
    assert error.value.status_code == 409


def test_autonomous_workflows_fail_closed_when_ollama_runtime_context_is_below_codex_requirement():
    root = Path(__file__).resolve().parents[1]
    context_gate = (root / "scripts" / "verify_ollama_context.ps1").read_text(encoding="utf-8")
    assert all("-MaximumRedirection 0" in line for line in context_gate.splitlines() if "$baseUrl/api/" in line and "Invoke-RestMethod" in line)
    production = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    diagnostic = (root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    # PowerShell accepts both Windows separators and forward slashes; compare
    # normalized paths so the contract is independent of YAML path spelling.
    production_paths = production.replace("\\", "/")
    diagnostic_paths = diagnostic.replace("\\", "/")

    assert "/api/create" in context_gate
    assert "/api/show" in context_gate
    assert "/api/generate" in context_gate
    assert "parameters = @{ num_ctx = $selectedContext }" in context_gate
    assert "PARAMETER\\s+num_ctx" in context_gate
    assert "keep_alive" in context_gate
    assert "/api/ps" in context_gate
    assert "context_length" in context_gate
    assert "if ($VerifyOnly) {" in context_gate
    assert context_gate.count("if ($VerifyOnly) {") == 1
    assert context_gate.count("OLLAMA_RUNTIME_CONTEXT_OK") == 1
    assert "if ($allocated -lt $selectedContext)" not in context_gate
    assert "-le 0" in context_gate
    verify_only = context_gate.split("if ($VerifyOnly) {", 1)[1].split("} else {", 1)[0]
    assert "$modelInfo.modelfile" in verify_only
    assert "PARAMETER\\s+num_ctx" in verify_only
    assert "[int64]$savedContext.Groups[1].Value -ne $selectedContext" in verify_only
    assert "if ($allocated -ne $selectedContext)" in context_gate
    assert "if ($allocated -lt $selectedContext)" not in context_gate
    assert "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH" in context_gate
    assert "Get-AutomaticContext" in context_gate
    assert "Assert-ToolCallingCapability" in context_gate
    assert "ollama_context.py" in context_gate
    assert "return [int64][Math]::Min($Maximum, 512)" not in context_gate
    assert "return [int64][Math]::Min($Maximum, 2048)" not in context_gate
    assert "AUTONOMOUS_ENGINEERING_MODEL=$codexModel" in context_gate
    assert production_paths.count("scripts/verify_ollama_context.ps1") == 2
    diagnostic_job = diagnostic_paths.split("  sandbox-diagnostic:", 1)[1]
    assert diagnostic_job.count("scripts/verify_ollama_context.ps1") == 2
    assert "-VerifyOnly" in production
    assert "-VerifyOnly" in diagnostic
    assert production_paths.index("scripts/verify_ollama_context.ps1") < production_paths.index("Verify Ollama and a real workspace-confined Codex tool call")
    assert diagnostic_paths.index("scripts/verify_ollama_context.ps1") < diagnostic_paths.index("Probe MXC and Codex exec_command")
    assert ".codex-sandbox-probe-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT).tmp" in production
    assert ".codex-sandbox-probe.tmp" not in production
    assert '--require-command-fragment "$probeFileName"' in production
    assert "Cancel superseded CI runs for this branch" in diagnostic
    assert '("tests.yml", "browser-e2e.yml", "dependency-audit.yml")' in diagnostic
    assert "if ($run.head_sha -eq $env:GITHUB_SHA) { continue }" in diagnostic
    assert "/cancel" in diagnostic and "/force-cancel" in diagnostic


def test_ollama_status_returns_every_installed_model_without_inserting_defaults(monkeypatch):
    names = [f"model-{index}:latest" for index in range(55)]
    selected = names[-1]

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                payload = {"models": [{"name": name} for name in names]}
            else:
                payload = {
                    "details": {"family": "llama"},
                    "model_info": {"llama.context_length": 32768},
                    "capabilities": ["completion", "tools"],
                }
            return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *handlers: FakeOpener())

    result = agent._ollama_status(selected)

    assert result["models"] == sorted(names)
    assert len(result["models"]) == 55
    assert "gpt-oss:20b" not in result["models"]
    assert result["model_installed"] is True
    assert result["model_metadata_available"] is True
    assert result["model_context_supported"] is False
    assert "hardware-safe context" in result["model_context_error"]


def test_ollama_status_keeps_server_online_when_selected_model_metadata_fails(monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"model:latest"}]}')
            raise OSError("metadata endpoint unavailable")

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *handlers: FakeOpener())

    result = agent._ollama_status("model:latest")

    assert result["online"] is True
    assert result["model_installed"] is True
    assert result["model_metadata_available"] is False
    assert result["model_context_supported"] is False
    assert result["model_supports_tools"] is False
    assert "OSError" in result["model_metadata_error"]


def test_ollama_status_accepts_large_valid_show_metadata_response(monkeypatch):
    from app import ollama

    model_info = {
        "llama.context_length": 131072,
        "llama.block_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.attention.key_length": 128,
        "llama.attention.value_length": 128,
    }
    large_payload = {
        "license": "x" * (9 * 1024 * 1024),
        "details": {"family": "llama"},
        "model_info": model_info,
        "capabilities": ["completion", "tools", "thinking"],
    }
    show_body = json.dumps(large_payload).encode("utf-8")

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"large-model:latest"}]}')
            return io.BytesIO(show_body)

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    from app import hardware
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: {"models": []})
    monkeypatch.setattr(ollama, "_model_memory_estimate", lambda *args: {
        "weights_gib": 1.0, "overhead_gib": 0.5,
        "kv_cache_bytes_per_token_upper_bound": 1024,
    })
    monkeypatch.setattr(ollama, "_recommended_context_tokens", lambda *args, **kwargs: 32768)
    monkeypatch.setattr(hardware, "detect_hardware", lambda: {})

    result = agent._ollama_status("large-model:latest")

    assert len(show_body) > 8 * 1024 * 1024
    assert len(show_body) < 32 * 1024 * 1024
    assert result["model_metadata_available"] is True
    assert result["model_context_length"] == 131072
    assert result["model_capabilities"] == ["completion", "thinking", "tools"]
    assert result["model_tools_metadata_available"] is True
    assert result["model_supports_tools"] is True
    assert result["model_context_supported"] is True
    assert result["model_recommended_context_length"] == 32768


def test_save_settings_rejects_model_without_explicit_tool_calling_support(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model_installed": True,
        "model_metadata_available": True, "model_context_supported": True, "model_supports_tools": False,
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: pytest.fail("GitHub settings must not be changed."))

    with pytest.raises(HTTPException, match="قابلیت tools") as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model="model:latest", interval_hours=4, execution_mode="continuous")
        )

    assert error.value.status_code == 422


def test_save_settings_rejects_uninstalled_model_before_persisting(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model_installed": False,
        "model_metadata_available": False, "model_supports_tools": False,
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: pytest.fail("GitHub settings must not be changed."))

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model="missing:latest", interval_hours=4, execution_mode="continuous")
        )

    assert error.value.status_code == 422


def test_app_and_runner_readiness_scripts_require_model_context_and_tool_capability():
    root = Path(__file__).resolve().parents[1]
    for name in ("check_autonomous_agent.ps1", "check_autonomous_runner.ps1"):
        script = (root / "scripts" / name).read_text(encoding="utf-8")
        assert "/api/show" in script
        assert all("-MaximumRedirection 0" in line for line in script.splitlines() if "http://127.0.0.1:11434" in line and "Invoke-RestMethod" in line)
        assert ".context_length" in script
        assert '"tools"' in script
        assert "Model context metadata" in script
        assert "-gt 0" in script
        assert "Model tool calling" in script

    provisioning = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    assert ".context_length" in provisioning
    assert all("-MaximumRedirection 0" in line for line in provisioning.splitlines() if "http://127.0.0.1:11434" in line and "Invoke-RestMethod" in line)
    assert "-le 0" in provisioning
    assert '"tools"' in provisioning

def test_master_engineering_prompt_requires_known_defect_closure_before_code_changes():
    from pathlib import Path

    prompt = (
        Path(__file__).resolve().parents[1] / "docs" / "MASTER_ENGINEERING_PROMPT.md"
    ).read_text(encoding="utf-8")

    assert "Before writing or changing any code" in prompt
    assert "every user-reported requirement, prior diagnosis, known defect, failed test, warning, limitation, and unresolved acceptance criterion" in prompt
    assert "do not proceed until the implementation and regression/integration tests prove the complete end-to-end contract" in prompt
    assert "stop and state exactly what prevents verification instead of guessing or claiming completion" in prompt


def test_ollama_context_powershell_bridge_uses_explicit_file_not_native_stdin():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "verify_ollama_context.ps1").read_text(encoding="utf-8")
    helper = (root / "app" / "ollama_context.py").read_text(encoding="utf-8")
    assert "--input-file" in script
    assert "$selectionInput | & python" not in script
    assert 'parser.add_argument("--input-file"' in helper


def test_dashboard_status_does_not_start_an_uncached_live_ollama_probe(monkeypatch):
    from app import autonomous_engineering_services as services

    services._TOOL_PROBE_CACHE.clear()
    probe_calls = []

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(json.dumps({
                    "models": [{
                        "name": "model:latest",
                        "digest": "sha256:dashboard-status",
                        "size": 1024,
                    }]
                }).encode("utf-8"))
            return io.BytesIO(json.dumps({
                "details": {"family": "llama"},
                "model_info": {
                    "llama.context_length": 65536,
                    "llama.block_count": 32,
                    "llama.attention.head_count_kv": 8,
                    "llama.attention.key_length": 128,
                    "llama.attention.value_length": 128,
                },
            }).encode("utf-8"))

    result = services.ollama_status(
        "model:latest",
        build_opener=lambda *args: FakeOpener(),
        maximum_context_fn=lambda info, architecture: info["llama.context_length"],
        supports_tool_calling_fn=lambda capabilities: "tools" in capabilities,
        is_context_variant=lambda model: False,
        recommended_context_fn=lambda model: 32768,
        tool_probe_fn=lambda payload: probe_calls.append(payload) or True,
        allow_live_tool_probe=False,
    )

    assert probe_calls == []
    assert result["model_context_supported"] is True
    assert result["model_supports_tools"] is False
    assert result["model_tools_verification_method"] == "probe_required"


def test_dashboard_status_reuses_recent_tool_probe_evidence_without_inference():
    from app import autonomous_engineering_services as services

    services._TOOL_PROBE_CACHE.clear()
    services._TOOL_PROBE_CACHE[("model:latest", "sha256:cached")] = (
        __import__("time").monotonic(),
        True,
    )

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(json.dumps({
                    "models": [{
                        "name": "model:latest",
                        "digest": "sha256:cached",
                        "size": 1024,
                    }]
                }).encode("utf-8"))
            return io.BytesIO(json.dumps({
                "details": {"family": "llama"},
                "model_info": {
                    "llama.context_length": 65536,
                    "llama.block_count": 32,
                    "llama.attention.head_count_kv": 8,
                    "llama.attention.key_length": 128,
                    "llama.attention.value_length": 128,
                },
            }).encode("utf-8"))

    result = services.ollama_status(
        "model:latest",
        build_opener=lambda *args: FakeOpener(),
        maximum_context_fn=lambda info, architecture: info["llama.context_length"],
        supports_tool_calling_fn=lambda capabilities: "tools" in capabilities,
        is_context_variant=lambda model: False,
        recommended_context_fn=lambda model: 32768,
        tool_probe_fn=lambda payload: pytest.fail("Dashboard status must not run inference."),
        allow_live_tool_probe=False,
    )

    assert result["model_supports_tools"] is True
    assert result["model_tools_verification_method"] == "live_tool_call_probe"


def test_collect_status_disables_live_tool_probe_for_status_polling(monkeypatch):
    observed = {}

    def fake_ollama_status(model):
        observed["allow_live_tool_probe"] = getattr(
            agent._STATUS_PROBE_CONTEXT, "allow_live_tool_probe", None
        )
        return {
            "online": True, "model": model, "model_installed": True,
            "models": [model], "model_context_length": 65536,
            "model_capabilities": ["completion", "tools"],
            "model_supports_tools": True, "model_metadata_available": True,
            "model_tools_metadata_available": True, "model_context_supported": True,
            "model_selectable": True, "model_is_codex_context_variant": False,
        }

    monkeypatch.setattr(agent, "_ollama_status", fake_ollama_status)
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "authenticated": True, "runs_available": True, "recent_runs": [],
        "settings": {"model": "model:latest"},
    })

    agent.collect_status()

    assert observed["allow_live_tool_probe"] is False
    assert not hasattr(agent._STATUS_PROBE_CONTEXT, "allow_live_tool_probe")


def test_collect_status_runs_independent_probes_concurrently(monkeypatch):
    from threading import Barrier

    barrier = Barrier(4)

    def github():
        barrier.wait(timeout=3)
        return {
            "authenticated": True, "runs_available": True, "recent_runs": [],
            "settings": {"model": "model:latest"},
        }

    def codex():
        barrier.wait(timeout=3)
        return {"installed": True, "version": "codex test"}

    def runner():
        barrier.wait(timeout=3)
        return {"running": True, "process_detected": True}

    def storage():
        barrier.wait(timeout=3)
        return True

    monkeypatch.setattr(agent, "_github_snapshot", github)
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True,
        "models": [model], "model_selectable": True,
        "model_is_codex_context_variant": False,
        "model_metadata_available": True, "model_context_supported": True,
        "model_tools_metadata_available": True, "model_supports_tools": True,
        "model_capabilities": ["tools"], "model_context_length": 32768,
    })
    monkeypatch.setattr(agent, "_codex_status", codex)
    monkeypatch.setattr(agent, "_runner_status", runner)
    monkeypatch.setattr(agent, "storage_accessible", storage)

    result = agent.collect_status()

    assert result["github"]["authenticated"] is True
    assert result["ollama"]["model"] == "model:latest"
    assert result["codex"]["installed"] is True
    assert result["runner"]["running"] is True
    assert result["checks"]["progress_storage_accessible"] is True



def test_collect_status_returns_with_a_degraded_snapshot_when_a_probe_stalls(monkeypatch):
    from threading import Event
    from time import monotonic

    release_runner = Event()
    runner_finished = Event()

    def stalled_runner():
        try:
            release_runner.wait(timeout=2)
            return {"running": True, "process_detected": True}
        finally:
            runner_finished.set()

    monkeypatch.setattr(agent, "_STATUS_PROBE_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(agent, "_GITHUB_STATUS_PROBE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True,
        "authenticated": True,
        "recent_runs": [],
        "runs_available": True,
        "settings": {"model": "model:latest"},
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", stalled_runner)
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True,
        "model": model,
        "model_installed": True,
        "model_selectable": True,
        "model_is_codex_context_variant": False,
        "models": [model],
        "selectable_models": [model],
        "model_metadata_available": True,
        "model_context_supported": True,
        "model_tools_metadata_available": True,
        "model_supports_tools": True,
        "model_capabilities": ["tools"],
        "model_context_length": 32768,
    })

    started = monotonic()
    result = agent.collect_status()
    elapsed = monotonic() - started

    assert elapsed < 1.5
    assert "runner" in result["status_probe_timeouts"]
    assert result["checks"]["runner_running"] is False
    assert result["checks"]["progress_storage_accessible"] is True
    assert result["ollama"]["model"] == "model:latest"

    release_runner.set()
    assert runner_finished.wait(timeout=1)


def test_collect_status_does_not_queue_probes_when_shared_pool_is_saturated(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import BoundedSemaphore, Event, Lock
    from time import monotonic

    capacity = 5
    executor = ThreadPoolExecutor(max_workers=capacity)
    release_probes = Event()
    all_probes_started = Event()
    started_count = 0
    started_lock = Lock()

    monkeypatch.setattr(agent, "_STATUS_EXECUTOR", executor)
    monkeypatch.setattr(agent, "_STATUS_EXECUTOR_SLOTS", BoundedSemaphore(capacity))
    monkeypatch.setattr(agent, "_STATUS_PROBE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(agent, "_GITHUB_STATUS_PROBE_TIMEOUT_SECONDS", 0.05)

    def block_probe(result):
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == capacity:
                all_probes_started.set()
        release_probes.wait(timeout=3)
        return result

    monkeypatch.setattr(agent, "_github_snapshot", lambda: block_probe({
        "authenticated": True, "runs_available": True, "recent_runs": [],
        "settings": {"model": "model:latest"},
    }))
    monkeypatch.setattr(agent, "_codex_status", lambda: block_probe({
        "installed": True, "version": "codex test",
    }))
    monkeypatch.setattr(agent, "_runner_status", lambda: block_probe({
        "running": False, "process_detected": False,
    }))
    monkeypatch.setattr(agent, "storage_accessible", lambda: block_probe(True))
    monkeypatch.setattr(agent, "_ollama_status", lambda model: block_probe({
        "online": True,
        "model": model,
        "model_installed": True,
        "model_selectable": True,
        "model_is_codex_context_variant": False,
        "models": [model],
        "selectable_models": [model],
        "model_metadata_available": True,
        "model_context_supported": True,
        "model_tools_metadata_available": True,
        "model_supports_tools": True,
        "model_capabilities": ["tools"],
        "model_context_length": 32768,
    }))

    try:
        started = monotonic()
        first_result = agent.collect_status()
        assert monotonic() - started < 1.5
        assert all_probes_started.wait(timeout=1)

        started = monotonic()
        second_result = agent.collect_status()
        assert monotonic() - started < 0.2
        assert set(second_result["status_probe_unavailable"]) == {
            "github", "codex", "runner", "storage", "ollama",
        }
        assert second_result["status_probe_timeouts"] == []
        assert first_result["status_probe_timeouts"]
    finally:
        release_probes.set()
        executor.shutdown(wait=True)


def test_dashboard_script_bounds_and_coalesces_status_refreshes():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    orchestrator = (static / "autonomous-engineering.js").read_text(encoding="utf-8")
    core = (static / "autonomous-engineering-core.js").read_text(encoding="utf-8")
    status = (static / "autonomous-engineering-status.js").read_text(encoding="utf-8")
    controls = (static / "autonomous-engineering-controls.js").read_text(encoding="utf-8")
    runs = (static / "autonomous-engineering-runs.js").read_text(encoding="utf-8")
    html = (static / "autonomous-engineering.html").read_text(encoding="utf-8")
    progress = (static / "autonomous-engineering-progress.js").read_text(encoding="utf-8")

    assert "new AbortController()" in core
    assert 'const defaultTimeoutMs = path === "status" ? 40000' in core
    assert "requestedTimeoutMs > 120000" in core
    assert "const timeoutMs = requestedTimeoutMs === undefined ? defaultTimeoutMs : requestedTimeoutMs" in core
    assert 'path.startsWith("progress") ? 20000 : 120000' in core
    assert "timeoutError.ambiguous = true" in core
    assert "statusRefreshPromise" in status
    assert "function refresh(force = false)" in status
    assert "if (force) return refreshAfterMutation()" in status
    assert "dispatchPendingUntil" in status
    assert "if (settingsSaving) return" in controls
    assert "if (!status.canStartDispatch()) return" in controls
    assert "status.setDispatchSubmitting(true)" in controls
    assert "status.setDispatchSubmitting(false)" in controls
    assert "setDispatchSubmitting(value)" in status
    assert "if (statusRefreshPromise) return statusRefreshPromise" in status
    assert "async function refreshAfterMutation()" in status
    assert "function syncDispatchControls()" in status
    assert "taskButton.disabled = !canStartManualTask()" in status
    assert "function canStartManualTask()" in status
    assert "function canStartDispatch()" in status
    assert "statusKnown && lastCanDispatch" in status
    assert "dispatchUnlockTimer = window.setTimeout" in status
    assert "function armDispatchUnlockTimer()" in status
    assert "item.point[line.key] !== null" in progress
    assert "engineeringPercent !== null" in progress
    assert "intervalMs: 30000" in orchestrator
    assert "refresh: () => status.refresh()" in orchestrator
    assert "window.setInterval(" not in orchestrator
    assert "intervalMs: 5000, maxIntervalMs: 120000" in orchestrator
    assert "refresh: () => progress.refresh()" in orchestrator
    assert "core.startPolling({" in orchestrator
    assert "window.setTimeout(refresh, 2500)" not in orchestrator + status + controls
    assert "statusMutationRefreshPromise" in status
    assert "dispatchButton.disabled = false" not in controls
    assert 'await response.text()' in core
    assert 'try { payload = await response.json(); } catch (_) {}' not in core
    assert 'setStatusMessage(data.paused ?' in status
    assert 'setMessage(data.paused ?' not in status
    assert 'addEventListener("click", () => refresh())' in status
    assert "cancelButton.disabled = true" not in status
    assert 'getActiveRunId: () => lastActiveRunId' in status
    assert 'JSON.stringify({run_id: runId})' in controls
    assert 'if (error.ambiguous || (error.status >= 500 && !error.retryable))' in controls
    assert 'host.replaceChildren();' not in runs[:runs.index('let runsRequestSequence')]
    assert 'const existing = new Map(' in runs
    assert 'if (!panel.isConnected || item.dataset.runId !== runId) return;' in runs
    assert 'result.cleaned === true ? "success" : "error"' in runs
    assert 'updatedAtValid' in progress
    assert 'window.addEventListener("autonomous-engineering:cleanup-completed", () => { void refreshProgress({queueIfBusy: true}); });' in progress
    assert '[1, 2, 4, 6, 12, 24].includes(Number(data.interval_hours))' in status
    assert 'agent-status-message' in html
    assert "." + chr(92) * 2 + "scripts" + chr(92) * 2 + "provision_autonomous_runner.ps1" not in html


def test_autonomous_run_administration_routes_are_registered():
    paths = {route.path for route in agent.router.routes}
    assert "/api/autonomous-engineering/runs" in paths
    assert "/api/autonomous-engineering/runs/{run_id}/logs" in paths
    assert "/api/autonomous-engineering/runs/{run_id}" in paths
    assert "/api/autonomous-engineering/cleanup" in paths


def test_workflow_log_sanitizer_redacts_common_credentials():
    source = (
        "Authorization: Bearer bearer-secret-value\\n"
        "Authorization: Basic dXNlcjpwYXNzd29yZA==\\n"
        "github_pat_0123456789abcdefghijklmnopqrstuvwxyz\\n"
        "sk-proj-0123456789abcdefghijklmnopqrstuv\\n"
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signaturelongtoken\\n"
        "password=hunter2 api_key=secret-value\\n"
    )
    result = agent._sanitize_workflow_log(source)
    assert "bearer-secret-value" not in result
    assert "dXNlcjpwYXNzd29yZA==" not in result
    assert "github_pat_0123456789abcdefghijklmnopqrstuvwxyz" not in result
    assert "sk-proj-0123456789abcdefghijklmnopqrstuv" not in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "signaturelongtoken" not in result
    assert "hunter2" not in result
    assert "secret-value" not in result
    assert "[REDACTED]" in result


def test_workflow_run_list_uses_fixed_server_selected_limit(monkeypatch):
    calls = []

    def api_json(gh, path, timeout=15):
        calls.append((gh, path, timeout))
        return {"workflow_runs": [{
            "id": 456,
            "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
            "status": "completed",
            "html_url": "https://github.com/example/run/456",
        }]}

    monkeypatch.setattr(agent, "_github_api_json", api_json)

    runs = agent._workflow_runs("gh")

    assert calls == [(
        "gh",
        "actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100",
        20,
    )]
    assert runs[0]["id"] == 456
    assert runs[0]["workflow_path"] == ".github/workflows/autonomous-codex-engineering.yml"


def test_workflow_run_list_reads_diagnostics_only_for_failed_runs(monkeypatch):
    diagnostic_reads = []
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "workflow_runs": [
            {"id": 1, "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main", "status": "in_progress"},
            {"id": 2, "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main", "status": "completed", "conclusion": "success"},
            {"id": 3, "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main", "status": "completed", "conclusion": "failure"},
        ]
    })
    monkeypatch.setattr(agent, "_read_run_diagnostics", lambda run_id: (
        diagnostic_reads.append(run_id) or {"diagnostics": [{"summary": "root cause"}]}
    ))
    runs = agent._workflow_runs("gh")
    assert [run["id"] for run in runs] == [1, 2, 3]
    assert diagnostic_reads == [3]
    assert runs[0]["failure_summary"] is None
    assert runs[1]["failure_summary"] is None
    assert runs[2]["failure_summary"] == "root cause"


def test_run_logs_include_failed_job_and_step_reasons(monkeypatch):
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [{
        "id": 123, "workflow_path": ".github/workflows/autonomous-codex-engineering.yml",
        "status": "completed", "conclusion": "failure", "url": "https://github.com/example/run/123",
    }])
    api_paths = []

    def api_json(gh, path, timeout=15):
        api_paths.append(path)
        if path == "actions/runs/123":
            return {
                "id": 123,
                "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/example/run/123",
            }
        if path == "actions/runs/123/jobs?per_page=100":
            return {
                "jobs": [{
                    "name": "codex", "status": "completed", "conclusion": "failure",
                    "steps": [{"name": "Install dependencies", "conclusion": "failure"}],
                }]
            }
        raise AssertionError(path)

    commands = []
    monkeypatch.setattr(agent, "_github_api_json", api_json)
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: (
        commands.append(command) or subprocess.CompletedProcess(
            command, 0, "Traceback: Authorization: Bearer hidden-token\\n", ""
        )
    ))

    result = agent.autonomous_engineering_run_logs(123)

    assert api_paths == ["actions/runs/123", "actions/runs/123/jobs?per_page=100"]
    assert commands[0][1:] == ["run", "view", "123", "--log", "--repo", agent._repository()]
    assert result["run"]["id"] == 123
    assert result["run"]["url"] == "https://github.com/example/run/123"
    assert result["failures"] == [{
        "name": "codex", "status": "completed", "conclusion": "failure",
        "failed_steps": [{"name": "Install dependencies", "conclusion": "failure"}],
    }]
    assert "hidden-token" not in result["logs"]
    assert "[REDACTED]" in result["logs"]


def test_verified_workflow_run_rejects_invalid_ids_before_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: calls.append(args) or {})
    for invalid_id in (0, -1, True):
        with pytest.raises(HTTPException) as error:
            agent._verified_workflow_run("gh", invalid_id)
        assert error.value.status_code == 422
    assert calls == []


def test_run_logs_reject_non_agent_workflow(monkeypatch):
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 123, "path": ".github/workflows/another.yml@refs/heads/main", "status": "completed",
    })
    with pytest.raises(HTTPException) as error:
        agent._verified_workflow_run("gh", 123)
    assert error.value.status_code == 404


def test_delete_run_refuses_active_workflow_run(monkeypatch):
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 123, "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "in_progress",
    })
    calls = []
    monkeypatch.setattr(agent, "_run", lambda *args, **kwargs: calls.append(args) or None)

    with pytest.raises(HTTPException) as error:
        agent.delete_autonomous_engineering_run(123)

    assert error.value.status_code == 409
    assert calls == []


def test_delete_completed_run_calls_workflow_run_delete_api(monkeypatch):
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "id": 123, "path": ".github/workflows/autonomous-codex-engineering.yml@refs/heads/main",
        "status": "completed",
    })
    calls = []
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: calls.append(command) or subprocess.CompletedProcess(
        command, 0, "", ""
    ))

    result = agent.delete_autonomous_engineering_run(123)

    assert result["deleted"] is True
    assert calls[0][1:] == [
        "api", "--method", "DELETE",
        "repos/cognitive-kernel/Personal-AI-assistant/actions/runs/123",
    ]


def test_cleanup_removes_only_ephemeral_progress_files_and_preserves_durable_state(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_is_windows", lambda: False)
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [])
    runtime_dir = tmp_path / "worktree"
    runtime_dir.mkdir()
    from app import autonomous_engineering_progress as progress
    monkeypatch.setattr(agent, "execution_state_directory", lambda: tmp_path)
    from app import autonomous_engineering_execution_lock as execution_lock
    monkeypatch.setattr(execution_lock, "execution_state_directory", lambda: tmp_path)
    monkeypatch.setattr(agent, "_agent_runtime_directory", lambda: runtime_dir)
    for name in ("events.jsonl", "current.json", "history.jsonl", "scorecard-baseline.json", "worktree-checkpoint-123-1.patch"):
        (tmp_path / name).write_text("preserve-or-remove", encoding="utf-8")
    for name in (".agent-request.json", ".agent-github-state.json", ".agent-progress.json"):
        (runtime_dir / name).write_text("temporary", encoding="utf-8")

    result = agent.cleanup_autonomous_engineering_temporary_data()

    assert result["cleaned"] is True
    assert result["files_removed"] == 5
    assert not (tmp_path / "events.jsonl").exists()
    assert not (tmp_path / "current.json").exists()
    assert not (runtime_dir / ".agent-request.json").exists()
    assert not (runtime_dir / ".agent-github-state.json").exists()
    assert not (runtime_dir / ".agent-progress.json").exists()
    assert (tmp_path / "history.jsonl").is_file()
    assert (tmp_path / "scorecard-baseline.json").is_file()
    assert (tmp_path / "worktree-checkpoint-123-1.patch").is_file()


def test_cleanup_failure_paths_are_reduced_to_logical_filenames(monkeypatch):
    monkeypatch.setattr(agent, "_is_windows", lambda: True)
    monkeypatch.setattr(agent, "cleanup_shared_progress_data", lambda: {
        "cleaned": False,
        "files_removed": 0,
        "bytes_removed": 0,
        "failed_files": [r"C:\\Users\\ExampleUser\\AppData\\Local\\state\\events.jsonl"],
        "preserved": [],
    })
    result = agent._cleanup_autonomous_engineering_temporary_data_locked()
    assert result["cleaned"] is False
    assert result["failed_files"] == ["events.jsonl"]
    assert "ExampleUser" not in json.dumps(result)


def test_cleanup_refuses_active_run_or_pending_task(monkeypatch, tmp_path):
    from app import autonomous_engineering_execution_lock as execution_lock

    monkeypatch.setattr(agent, "_is_windows", lambda: False)
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "execution_state_directory", lambda: tmp_path)
    monkeypatch.setattr(execution_lock, "execution_state_directory", lambda: tmp_path)
    (tmp_path / "events.jsonl").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [{"status": "in_progress"}])
    with pytest.raises(HTTPException) as active_error:
        agent.cleanup_autonomous_engineering_temporary_data()
    assert active_error.value.status_code == 409
    assert (tmp_path / "events.jsonl").exists()

    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [])
    (tmp_path / "pending-manual-task.txt").write_text("pending", encoding="utf-8")
    with pytest.raises(HTTPException) as pending_error:
        agent.cleanup_autonomous_engineering_temporary_data()
    assert pending_error.value.status_code == 409
    assert (tmp_path / "events.jsonl").exists()

def test_run_management_controls_are_present_and_wired():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    script = (root / "app" / "static" / "autonomous-engineering-runs.js").read_text(encoding="utf-8")
    assert 'id="refresh-runs"' in page
    assert 'id="cleanup-agent-temp"' in page
    assert 'id="cleanup-agent-status"' in page
    assert 'dataset.runAction = "logs"' in script
    assert 'dataset.runAction = "delete"' in script
    assert 'request("cleanup", {method: "POST"})' in script
    assert 'request("runs/" + runId + "/logs")' in script
    assert 'request("runs/" + runId, {method: "DELETE"})' in script




def test_run_diagnostics_are_loaded_from_shared_state_and_sanitized(monkeypatch):
    from app import autonomous_engineering_state_broker as broker

    payload = {
        "schema_version": 1,
        "run_id": "123",
        "updated_at": "2026-11-17T12:00:00+00:00",
        "diagnostics": [{
            "run_attempt": "1",
            "task": 1,
            "attempt": 2,
            "stage": "codex_decision",
            "category": "task_blocked",
            "summary": "Codex marked task 1 as blocked.",
            "detail": "The policy check rejected the requested action. ghp_0123456789abcdefghijklmnopqrstuvwxyz",
            "recorded_at": "2026-11-17T12:00:00+00:00",
        }],
    }
    monkeypatch.setattr(
        broker, "diagnostics", lambda run_id: payload if run_id == 123 else None
    )

    result = agent._read_run_diagnostics(123)

    assert result is not None
    assert result["run_id"] == 123
    assert result["diagnostics"][0]["stage"] == "codex_decision"
    assert "policy check rejected" in result["diagnostics"][0]["detail"]
    assert "ghp_0123456789abcdefghijklmnopqrstuvwxyz" not in result["diagnostics"][0]["detail"]
    assert agent._read_run_diagnostics(0) is None
    assert agent._read_run_diagnostics(True) is None

def test_workflow_failure_summary_explains_missing_codex_final_agent_message():
    raw_log = (
        "Warning: no last agent message; wrote empty content to codex-report.md\\n"
        "Codex final report validation failed: completion marker count was 0.\\n"
        "Autonomous engineering cycle failed: RuntimeError: bounded retry budget exhausted."
    )

    result = agent._workflow_failure_summary(raw_log, [])

    assert result is not None
    assert "Codex did not emit a final agent message" in result
    assert "output-last-message report was empty" in result
    assert "durable task/checkpoint" in result


def test_workflow_failure_summary_prefers_controller_root_cause():
    raw_log = (
        "##[error]Process completed with exit code 1.\n"
        "Autonomous engineering cycle failed: RuntimeError: Codex reported task 1 as blocked; record the blocker.\n"
    )

    result = agent._workflow_failure_summary(raw_log, [{
        "name": "codex",
        "failed_steps": [{"name": "Execute one isolated task", "conclusion": "failure"}],
    }])

    assert result == "RuntimeError: Codex reported task 1 as blocked; record the blocker."


def test_run_logs_include_persisted_codex_reason_and_failure_stage(monkeypatch):
    diagnostic = {
        "run_id": 123,
        "updated_at": "2026-11-17T12:00:00+00:00",
        "diagnostics": [{
            "run_attempt": "1",
            "task": 1,
            "attempt": 1,
            "stage": "codex_decision",
            "category": "task_blocked",
            "summary": "Codex marked task 1 as blocked.",
            "detail": "The final explanation says the selected operation requires unavailable approval.",
            "recorded_at": "2026-11-17T12:00:00+00:00",
        }],
    }
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_verified_workflow_run", lambda gh, run_id: {
        "id": 123, "status": "completed", "conclusion": "failure",
        "url": "https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/123",
    })
    monkeypatch.setattr(agent, "_github_api_json", lambda *args, **kwargs: {
        "jobs": [{
            "name": "codex", "status": "completed", "conclusion": "failure",
            "steps": [{"name": "Execute task", "conclusion": "failure"}],
        }]
    })
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: subprocess.CompletedProcess(
        command, 0, "Autonomous engineering cycle failed: generic controller message", "",
    ))
    monkeypatch.setattr(agent, "_read_run_diagnostics", lambda run_id: diagnostic)

    result = agent.autonomous_engineering_run_logs(123)

    assert result["error_summary"] == "Codex marked task 1 as blocked."
    assert result["failure_stage"] == "codex_decision"
    assert result["diagnostic"]["diagnostics"][0]["detail"].startswith("The final explanation")
    assert result["failures"][0]["failed_steps"][0]["name"] == "Execute task"


def test_dashboard_renders_failure_summary_and_codex_diagnostic_details():
    script = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "autonomous-engineering-runs.js"
    ).read_text(encoding="utf-8")

    assert "علت خطا: " in script
    assert "data.error_summary" in script
    assert "data.diagnostic.diagnostics" in script
    assert "گزارش توضیحی Codex / جزئیات خطا" in script
    assert "گزارش خام GitHub" in script



def _readiness_with_checkpoint(checkpoint):
    return {
        "ready": True,
        "paused": False,
        "can_dispatch": True,
        "github": {"runs_available": True, "recent_runs": []},
        "pending_work": {
            "available": True,
            "blocked": True,
            "markers": {
                "pending_task": False,
                "pending_owner": False,
                "pending_pr": False,
                "cleanup_in_progress": False,
            },
            "checkpoint": checkpoint,
            "reason": "checkpoint",
        },
    }


def test_manual_dispatch_rejects_new_task_when_verified_checkpoint_is_pending(monkeypatch):
    checkpoint = {
        "available": True,
        "verified": True,
        "run_id": "44269615724",
        "task": 1,
        "base_sha": "a" * 40,
        "updated_at": 1763452800.0,
        "patch_bytes": 4096,
    }
    monkeypatch.setattr(agent, "collect_status", lambda: _readiness_with_checkpoint(checkpoint))
    monkeypatch.setattr(
        agent.shutil, "which",
        lambda _: (_ for _ in ()).throw(AssertionError("blocked manual task must not reach GitHub CLI")),
    )

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task="شروع وظیفهٔ تازه"))

    assert error.value.status_code == 409
    assert "checkpoint" in error.value.detail
    assert "44269615724" in error.value.detail


def test_empty_dispatch_resumes_verified_checkpoint(monkeypatch):
    calls = []
    checkpoint = {
        "available": True,
        "verified": True,
        "run_id": "44269615724",
        "task": 1,
        "base_sha": "a" * 40,
        "updated_at": 1763452800.0,
        "patch_bytes": 4096,
    }
    monkeypatch.setattr(agent, "collect_status", lambda: _readiness_with_checkpoint(checkpoint))
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.dispatch_autonomous_engineering()

    assert result["accepted"] is True
    assert result["manual_task_submitted"] is False
    assert calls[-1][-2:] == ["--raw-field", "manual_task="]


def test_dispatch_fails_closed_when_checkpoint_integrity_is_unverified(monkeypatch):
    checkpoint = {
        "available": True,
        "verified": False,
        "reason": "checkpoint_patch_size_mismatch",
    }
    monkeypatch.setattr(agent, "collect_status", lambda: _readiness_with_checkpoint(checkpoint))
    monkeypatch.setattr(
        agent.shutil, "which",
        lambda _: (_ for _ in ()).throw(AssertionError("unverified checkpoint must block every dispatch")),
    )

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert error.value.status_code == 409
    assert "یکپارچگی" in error.value.detail


def test_dispatch_failure_exposes_correlation_id_and_redacts_cli_secrets(monkeypatch, caplog):
    token = "ghp_123456789012345678901234567890"
    monkeypatch.setattr(
        agent, "collect_status",
        lambda: {"ready": True, "github": {"runs_available": True, "recent_runs": []}},
    )
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    calls = []

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", f"HTTP 401 Bearer {token}")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert len(calls) == 2
    assert error.value.status_code == 502
    assert error.value.headers["X-Error-Code"] == "WORKFLOW_DISPATCH_FAILED"
    assert error.value.headers["X-Retryable"] == "false"
    correlation_id = error.value.headers["X-Correlation-ID"]
    assert len(correlation_id) == 32
    assert correlation_id in error.value.detail
    assert "exit code 1" in error.value.detail
    assert "پیش از تلاش مجدد" in error.value.detail
    assert token not in caplog.text
    assert "stderr_sha256=" in caplog.text
    assert token not in caplog.text



def test_dispatch_launch_error_is_definitive_and_retryable(monkeypatch):
    monkeypatch.setattr(
        agent, "collect_status",
        lambda: {"ready": True, "github": {"runs_available": True, "recent_runs": []}},
    )
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert raise_os_error is True
        raise FileNotFoundError("gh executable disappeared after lookup")

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert error.value.status_code == 503
    assert error.value.headers["X-Error-Code"] == "WORKFLOW_DISPATCH_LAUNCH_FAILED"
    assert error.value.headers["X-Retryable"] == "true"
    assert error.value.headers["X-Correlation-ID"] in error.value.detail
    assert "می‌توانید دوباره تلاش کنید" in error.value.detail
    assert "ممکن است GitHub درخواست را دریافت کرده باشد" not in error.value.detail

def test_dispatch_timeout_reports_ambiguous_outcome_without_encouraging_retry(monkeypatch):
    monkeypatch.setattr(
        agent, "collect_status",
        lambda: {"ready": True, "github": {"runs_available": True, "recent_runs": []}},
    )
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return None

    monkeypatch.setattr(agent, "_run", fake_run)

    with pytest.raises(HTTPException) as error:
        agent.dispatch_autonomous_engineering()

    assert error.value.status_code == 502
    assert error.value.headers["X-Error-Code"] == "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN"
    assert error.value.headers["X-Retryable"] == "false"
    assert error.value.headers["X-Correlation-ID"] in error.value.detail
    assert "نامشخص" in error.value.detail
    assert "پیش از تلاش مجدد" in error.value.detail



def test_successful_dispatch_correlates_ui_request_and_workflow_input(monkeypatch):
    correlation_id = "d" * 32
    calls = []
    monkeypatch.setattr(agent, "_LAST_DISPATCH_ACCEPTED_AT", 0.0)
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True,
        "github": {"runs_available": True, "recent_runs": []},
        "pending_work": {"available": True, "blocked": False, "markers": {}},
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8, *, raise_os_error=False):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post(
        "/api/autonomous-engineering/dispatch",
        json={},
        headers={"X-Correlation-ID": correlation_id},
    )

    assert response.status_code == 202
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json()["correlation_id"] == correlation_id
    assert calls[-1][-4:] == [
        "--raw-field", "correlation_id=" + correlation_id,
        "--raw-field", "manual_task=",
    ]


def test_real_autonomous_dispatch_http_error_exposes_structured_diagnostics(monkeypatch):
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": False,
        "paused": False,
        "github": {"runs_available": True, "recent_runs": []},
    })

    response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post(
        "/api/autonomous-engineering/dispatch",
        json={},
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "AUTONOMOUS_ENGINEERING_CONFLICT"
    assert payload["retryable"] is False
    assert payload["correlation_id"]
    assert payload["message"]
    assert payload["safe_details"] == {}
    assert response.headers["X-Error-Code"] == payload["code"]
    assert response.headers["X-Correlation-ID"] == payload["correlation_id"]
    assert response.headers["X-Retryable"] == "false"


def test_cleanup_refuses_when_repository_execution_lock_is_owned(monkeypatch):
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [])
    if agent._is_windows():
        from app.autonomous_engineering_state_broker import StateBrokerConflict
        monkeypatch.setattr(
            agent,
            "cleanup_shared_progress_data",
            lambda: (_ for _ in ()).throw(
                StateBrokerConflict("repository lock busy", ["repository_execution_active"])
            ),
        )
    else:
        monkeypatch.setattr(
            agent,
            "repository_execution_lock",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("Another autonomous execution owns this repository worktree.")
            ),
        )
    if not agent._is_windows():
        monkeypatch.setattr(
            agent,
            "_cleanup_autonomous_engineering_temporary_data_locked",
            lambda: pytest.fail("cleanup must not run while another execution owns the worktree"),
        )

    response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post(
        "/api/autonomous-engineering/cleanup",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "AUTONOMOUS_ENGINEERING_CONFLICT"


def test_cleanup_windows_uses_broker_active_state_check_instead_of_stale_github_preflight(monkeypatch):
    calls = {"runs": 0, "cleanup": 0}
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")

    if agent._is_windows():
        from app.autonomous_engineering_state_broker import StateBrokerConflict
        monkeypatch.setattr(agent, "_workflow_runs", lambda gh: (
            calls.__setitem__("runs", calls["runs"] + 1) or pytest.fail("Windows broker owns the authoritative active-progress check")
        ))
        monkeypatch.setattr(agent, "cleanup_shared_progress_data", lambda: (
            calls.__setitem__("cleanup", calls["cleanup"] + 1)
            or (_ for _ in ()).throw(StateBrokerConflict(
                "active progress run", ["active_progress_run"]
            ))
        ))
        response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post("/api/autonomous-engineering/cleanup")
        assert response.status_code == 409
        assert calls == {"runs": 0, "cleanup": 1}
    else:
        monkeypatch.setattr(agent, "repository_execution_lock", lambda *args, **kwargs: nullcontext())
        def workflow_runs(gh):
            calls["runs"] += 1
            return [] if calls["runs"] == 1 else [{"status": "in_progress"}]
        monkeypatch.setattr(agent, "_workflow_runs", workflow_runs)
        monkeypatch.setattr(agent, "_cleanup_autonomous_engineering_temporary_data_locked", lambda: (
            calls.__setitem__("cleanup", calls["cleanup"] + 1) or {"cleaned": True}
        ))
        response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post("/api/autonomous-engineering/cleanup")
        assert response.status_code == 409
        assert calls == {"runs": 2, "cleanup": 0}

def test_windows_cleanup_delegates_locking_to_broker_without_client_self_lock(monkeypatch):
    monkeypatch.setattr(agent, "_is_windows", lambda: True)
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(
        agent,
        "_workflow_runs",
        lambda gh: pytest.fail("Windows broker performs the active-progress check under its own locks"),
    )
    monkeypatch.setattr(
        agent,
        "repository_execution_lock",
        lambda *args, **kwargs: pytest.fail("Windows broker must acquire the shared repository lock"),
    )
    monkeypatch.setattr(agent, "cleanup_shared_progress_data", lambda: {"cleaned": True})

    response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post(
        "/api/autonomous-engineering/cleanup",
    )

    assert response.status_code == 200
    assert response.json()["cleaned"] is True

def test_windows_cleanup_preserves_worktree_runtime_files_after_broker_release(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_is_windows", lambda: True)
    runtime_dir = tmp_path / "worktree"
    runtime_dir.mkdir()
    for name in agent._RUNTIME_CLEANUP_FILES:
        (runtime_dir / name).write_text("runtime", encoding="utf-8")
    monkeypatch.setattr(agent, "_agent_runtime_directory", lambda: runtime_dir)
    monkeypatch.setattr(
        agent,
        "cleanup_shared_progress_data",
        lambda: {
            "cleaned": True,
            "files_removed": 2,
            "bytes_removed": 12,
            "failed_files": [],
            "preserved": ["history.jsonl", "checkpoint files"],
        },
    )

    result = agent._cleanup_autonomous_engineering_temporary_data_locked()

    assert result["cleaned"] is True
    assert result["files_removed"] == 2
    assert set(agent._RUNTIME_CLEANUP_FILES) <= set(result["preserved"])
    assert all((runtime_dir / name).is_file() for name in agent._RUNTIME_CLEANUP_FILES)

def test_posix_cleanup_lock_order_is_repository_then_state_then_checkpoint(monkeypatch, tmp_path):
    from contextlib import contextmanager

    calls = []
    monkeypatch.setattr(agent, "_is_windows", lambda: False)
    monkeypatch.setattr(agent, "_authenticated_gh", lambda: "gh")
    monkeypatch.setattr(agent, "_workflow_runs", lambda gh: [])
    monkeypatch.setattr(agent, "execution_state_directory", lambda: tmp_path)
    monkeypatch.setattr(agent, "_agent_runtime_directory", lambda: tmp_path / "worktree")
    (tmp_path / "worktree").mkdir()

    @contextmanager
    def repository_lock(*_args, **_kwargs):
        calls.append("repository")
        yield

    @contextmanager
    def state_lock(*_args, **_kwargs):
        calls.append("durable-state")
        yield

    @contextmanager
    def checkpoint_lock(*_args, **_kwargs):
        calls.append("checkpoint")
        yield

    monkeypatch.setattr(agent, "repository_execution_lock", repository_lock)
    monkeypatch.setattr(agent, "durable_state_lock", state_lock)
    monkeypatch.setattr(agent, "checkpoint_store_lock", checkpoint_lock)

    response = TestClient(main_module.app, client=("127.0.0.1", 12345)).post(
        "/api/autonomous-engineering/cleanup",
    )

    assert response.status_code == 200
    assert calls == ["repository", "durable-state", "checkpoint"]




def test_schedule_gate_suppresses_cross_process_duplicate_workflow_dispatches():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-engineering.yml"
    ).read_text(encoding="utf-8")
    schedule_gate = workflow[
        workflow.index("  schedule-gate:") : workflow.index("\n  codex:")
    ]

    assert "actions/workflows/autonomous-codex-engineering.yml/runs?per_page=30" in schedule_gate
    assert "int(run.get(\"id\", 0)) != current_id" in schedule_gate
    assert 'run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}' in schedule_gate
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" && -n "$MANUAL_TASK" ]]' in schedule_gate
    assert "no durable state was changed" in schedule_gate
    assert "Another autonomous run is already active or queued" in schedule_gate
    codex_job = workflow[workflow.index("  codex:") :]
    assert "group: autonomous-local-engineering-${{ github.repository }}" in codex_job
    assert "cancel-in-progress: false" in codex_job

def test_autonomous_workflow_rechecks_live_pause_variable_after_concurrency_admission():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-engineering.yml"
    ).read_text(encoding="utf-8")
    assert "AUTONOMOUS_ENGINEERING_RECOVERY_ATTEMPT:" in workflow
    start = workflow.index("- name: Recheck live Pause and execution mode after concurrency admission")
    end = workflow.index("- name: Execute one isolated task, validate, and publish for owner review", start)
    gate = workflow[start:end]
    concurrency = workflow.index("group: autonomous-local-engineering-${{ github.repository }}")

    assert concurrency < start
    assert "$repositoryUri = \"https://api.github.com/repos/$env:GITHUB_REPOSITORY\"" in gate
    assert "$repositoryMetadata = Invoke-RestMethod -Method Get" in gate
    assert "$repositoryMetadata.full_name" in gate
    assert gate.index("$repositoryMetadata = Invoke-RestMethod") < gate.index('Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_PAUSED"')
    assert "function Get-LiveRepositoryVariable" in gate
    assert "/actions/variables/$Name" in gate
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_PAUSED"' in gate
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_MODE"' in gate
    assert "EVENT_NAME" in gate and "RECOVERY_ATTEMPT" in gate
    assert "RESUME_ONCE" in gate and "unmarkedEmptyDispatch" in gate
    assert "resume_once:" in workflow
    assert "AUTONOMOUS_ENGINEERING_RESUME_ONCE:" in workflow
    assert "AUTONOMOUS_ENGINEERING_MANUAL_TASK:" in workflow
    assert "modeValue -eq \"scheduled\"" in gate
    assert "Invoke-RestMethod -Method Get" in gate
    assert 'VARIABLES_TOKEN: ${{ secrets.AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN }}' in gate
    assert '$env:VARIABLES_TOKEN' in gate
    assert 'Remove-Item Env:VARIABLES_TOKEN' in gate
    assert 'Repository secret AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN is missing' in gate
    assert 'No task will start.' in gate
    assert 'Variables: read' in gate
    assert 'Authorization = "Bearer $settingsToken"' in gate
    assert '$headers.Authorization = $null' in gate
    assert '$settingsToken = $null' in gate
    assert 'Authorization = "Bearer $env:GH_TOKEN"' not in gate
    assert 'GH_TOKEN: ${{ github.token }}' not in gate
    assert 'auth token --hostname github.com' not in gate
    assert 'if ($statusCode -eq 404)' in gate
    assert "Failing closed; no new task will start." in gate
    assert "[System.IO.File]::AppendAllText" in gate
    assert "[System.Text.UTF8Encoding]::new($false)" in gate
    assert "Out-File" not in gate
    assert "PAUSED: ${{ vars.AUTONOMOUS_ENGINEERING_PAUSED" not in gate
    assert "EXECUTION_MODE: ${{ vars.AUTONOMOUS_ENGINEERING_MODE" not in gate
    execute_step = workflow[end:workflow.index("- name: Verify persistent Ollama context after autonomous Codex task", end)]
    assert "AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN: ${{ secrets.AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN }}" in execute_step
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "if: steps.pause.outcome == 'success' && steps.pause.outputs.skip != 'true'" in execute_step
    cycle_policy = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "autonomous_engineering_cycle.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.pop("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "")' in cycle_policy
    run_cycle_start = cycle_policy.index("def run_cycle(")
    assert cycle_policy.index('os.environ.pop("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "")', run_cycle_start) < cycle_policy.index("run_codex(", run_cycle_start)
    assert "_live_pause_enabled(settings_token, repo)" in cycle_policy
    assert "_live_execution_mode(settings_token, repo)" in cycle_policy
    assert "def _settings_api(" in cycle_policy
    assert 'return api(settings_token, repo, "GET", path)' in cycle_policy


def test_one_shot_resume_bypasses_only_stale_pause_snapshot_before_live_gate():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-engineering.yml"
    ).read_text(encoding="utf-8")
    gate_start = workflow.index("- name: Apply the configured daily execution frequency")
    gate_end = workflow.index("\n  codex:", gate_start)
    schedule_gate = workflow[gate_start:gate_end]

    assert "RESUME_ONCE: ${{ inputs.resume_once || false }}" in schedule_gate
    assert 'if [[ "$PAUSED" == "true" ]]; then' in schedule_gate
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" && "$RESUME_ONCE" == "true" ]]; then' in schedule_gate
    assert "bypasses only the stale workflow-context Pause snapshot" in schedule_gate
    assert "live gate after concurrency admission remains authoritative" in schedule_gate
    assert "A manual task was not accepted because autonomous engineering is paused" in schedule_gate

    live_gate = workflow.index("Recheck live Pause and execution mode after concurrency admission")
    assert gate_start < live_gate
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_PAUSED"' in workflow[live_gate:]
    assert 'Get-LiveRepositoryVariable -Name "AUTONOMOUS_ENGINEERING_MODE"' in workflow[live_gate:]
    assert "if: steps.pause.outcome == 'success' && steps.pause.outputs.skip != 'true'" in workflow[live_gate:]
