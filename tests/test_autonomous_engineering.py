from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import autonomous_engineering as agent


def test_status_readiness_requires_every_local_dependency(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_REPOSITORY", "cognitive-kernel/Personal-AI-assistant")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model], "model_context_length": 65536, "model_capabilities": ["completion", "tools"], "model_supports_tools": True, "model_metadata_available": True, "model_context_supported": True, "model_selectable": True, "model_is_codex_context_variant": False
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

    def fake_run(command, timeout=8):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent.dispatch_autonomous_engineering()

    assert result["accepted"] is True
    assert calls[0][1:] == ["auth", "status", "--hostname", "github.com"]
    assert calls[1][1:] == [
        "workflow", "run", "autonomous-codex-engineering.yml",
        "--repo", "cognitive-kernel/Personal-AI-assistant", "--ref", "main",
        "--field", "manual_task=",
    ]


def test_autonomous_engineering_page_and_navigation_are_present():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    main = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    learning = (root / "app" / "ai_learning" / "static" / "index.html").read_text(encoding="utf-8")
    agent_page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    agent_script = (root / "app" / "static" / "autonomous-engineering.js").read_text(encoding="utf-8")

    assert 'href="/autonomous-engineering"' in main
    assert 'href="/autonomous-engineering"' in learning
    assert 'href="/ai-learning"' in page
    assert 'href="/autonomous-engineering" aria-current="page"' in page
    assert 'id="dispatch-agent"' in page
    assert 'id="cancel-agent"' in page
    assert 'id="pause-agent"' in page
    assert 'id="resume-agent"' in page
    assert "OPENAI_API_KEY" not in page
    assert "حساب Windows اختصاصی و غیرمدیر" in agent_page
    assert "checks.github_runs_available" in agent_script
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
    assert "checks.progress_storage_accessible" in agent_script
    assert "gpt-oss:20b" not in page
    assert 'هیچ مدل اصلی قابل انتخابی از Ollama شناسایی نشد' in agent_script
    assert "model_context_length" in agent_script
    assert "model_supports_tools" in agent_script
    assert "model_selectable" in agent_script
    assert "نسخهٔ داخلی Context" in agent_script
    assert "renderModels(data.models || data.selectable_models" in agent_script
    assert "/-codex-ctx[0-9]+$/i.test(name)" in agent_script


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
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))

    result = agent.pause_autonomous_engineering()

    assert result["paused"] is True
    assert calls[0][1:3] == ["auth", "status"]
    assert calls[1][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED",
        "--body", "true", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]


def test_resume_clears_pause_and_dispatches_without_legacy_failure_counter(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_run", lambda command, timeout=8: calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))

    result = agent.resume_autonomous_engineering()

    assert result["paused"] is False
    assert calls[1][2:5] == ["set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body"]
    assert calls[2][1:3] == ["workflow", "run"]
    assert all("AUTONOMOUS_ENGINEERING_FAILURE_STREAK" not in call for call in calls)


def test_autonomous_recovery_workflow_retries_continuous_failures_without_an_automatic_retry_limit():
    root = Path(__file__).resolve().parents[1]
    recovery = (root / ".github" / "workflows" / "autonomous-codex-recovery.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in recovery
    assert "AUTONOMOUS_ENGINEERING_FAILURE_STREAK" in recovery
    assert "AUTONOMOUS_ENGINEERING_PENDING_TASK" in recovery
    assert "AUTONOMOUS_ENGINEERING_PAUSED" in recovery
    assert "continuous" in recovery
    assert "/dispatches" in recovery
    assert "delay_seconds = min(240, 30 * (2 ** min(streak - 1, 3)))" in recovery
    assert "streak >= 4" not in recovery


def test_ollama_status_bypasses_environment_proxies(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured.setdefault("requests", []).append((request.full_url, timeout))
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"gpt-oss:20b"}]}')
            return io.BytesIO(
                b'{"details":{"family":"llama"},"model_info":{"llama.context_length":65536},'
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
    assert result["model_supports_tools"] is True
    assert result["model_context_supported"] is True


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

    def fake_run(command, timeout=8):
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

    def fake_run(command, timeout=8):
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

    def fake_run(command, timeout=8):
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

    def fake_run(command, timeout=8):
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

    def fake_run(command, timeout=8):
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
    dashboard = (root / "app" / "static" / "autonomous-engineering.js").read_text(encoding="utf-8")
    assert "ابتدا مدل نصب‌شده را انتخاب کنید" in dashboard
    assert "installed.includes(selectedModel)" in dashboard
    assert "هنوز مدلی ذخیره نشده است" in dashboard
    assert "مدل ذخیره‌شده روی این سیستم نصب نیست" in dashboard


def test_github_snapshot_reads_saved_model_and_frequency(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
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


def test_progress_reader_uses_scorecard_baseline_and_date_filtered_snapshots(monkeypatch, tmp_path):
    from app import autonomous_engineering_progress as progress

    scorecard = tmp_path / "ACCEPTANCE_SCORECARD.md"
    scorecard.write_text(
        "**Snapshot:** 2026-11-03\n"
        "**Scored baseline:** main at " + "c" * 40 + "\n"
        "- **Engineering completion:** score = 62.00%\n"
        "- **Overall acceptance progress:** score = 59.00%\n",
        encoding="utf-8",
    )
    storage = tmp_path / "telemetry"
    storage.mkdir()
    (storage / "scorecard-baseline.json").write_text(json.dumps({
        "date": "2026-11-01",
        "engineering_percent": 60.47,
        "overall_percent": 57.45,
        "sha": "a" * 40,
        "source": "scorecard-baseline",
    }), encoding="utf-8")
    (storage / "history.jsonl").write_text(
        json.dumps({
            "type": "project_snapshot", "date": "2026-11-02",
            "recorded_at": "2026-11-02T10:00:00+00:00",
            "engineering_percent": 61.0, "overall_percent": 58.0, "sha": "b" * 40,
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(progress, "_DEFAULT_SCORECARD", scorecard)
    monkeypatch.setattr(progress, "_progress_dir", lambda: storage)

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
    script = (root / "app" / "static" / "autonomous-engineering.js").read_text(encoding="utf-8")
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
    assert "شروع صف پیوسته" in script
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
    assert "actions/variables" not in workflow
    assert '"${RunnerAccount}:(OI)(CI)M"' in acl_script
    assert '"${AppAccount}:(OI)(CI)RX"' in acl_script
    assert "/inheritance:r /T /C /grant:r" in acl_script
    assert 'window.setInterval(refreshProgress, 5000)' in script
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
    assert result["phase_label"] == "در انتظار Runner ویندوز"
    assert result["status"] == "queued"


def test_dispatch_forwards_manual_task_as_a_single_untrusted_field(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "collect_status", lambda: {
        "ready": True, "github": {"runs_available": True, "recent_runs": []}
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    task = "بررسی Issue #52؛ عبارت ; gh secret list نباید اجرا شود"

    result = agent.dispatch_autonomous_engineering(agent.ManualTaskRequest(task=task))

    assert result["accepted"] is True
    assert calls[-1][-2:] == ["--field", "manual_task=" + task]


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

    report.write_text("TASK_COMPLETE\nQUEUE_EMPTY\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly one"):
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

    def fake_run(command, timeout=8):
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


def test_cancel_finds_active_run_after_ten_newer_completed_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    runs = [
        {"databaseId": index + 1, "status": "completed"}
        for index in range(30)
    ]
    runs[-1] = {"databaseId": 999, "status": "in_progress"}

    def fake_run(command, timeout=8):
        calls.append(command)
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(runs), "")
        if command[1:3] == ["run", "cancel"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent.cancel_autonomous_engineering()

    assert result["run_id"] == 999
    list_command = next(command for command in calls if command[1:3] == ["run", "list"])
    assert list_command[list_command.index("--limit") + 1] == "30"
    assert calls[-1][1:3] == ["run", "cancel"]
    assert calls[-1][3] == "999"


def test_autonomous_workflows_enforce_positive_and_exact_ollama_runtime_context():
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
    # VerifyOnly has two intentional branches: persisted-model verification before
    # runtime inspection, and model release after the verification-only probe.
    assert context_gate.count("if ($VerifyOnly) {") == 2
    assert context_gate.count("OLLAMA_RUNTIME_CONTEXT_OK") == 1
    assert "if ($allocated -lt $selectedContext)" not in context_gate
    assert "-lt $minimumContextTokens" in context_gate
    assert "64000" not in context_gate
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
    assert "$minimumContextTokens = 1" in context_gate
    assert "64000" not in context_gate
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

    show_requests = []

    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                payload = {"models": [{"name": name} for name in names]}
            else:
                show_requests.append(json.loads(request.data.decode("utf-8")))
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
    assert result["model_context_supported"] is True
    assert result["model_context_error"] is None
    assert show_requests == [{"model": selected, "verbose": False}]


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


def test_save_settings_accepts_installed_model_with_context_below_64k(monkeypatch):
    saved = []
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True,
        "model_installed": True,
        "model_metadata_available": True,
        "model_context_supported": True,
        "model_context_length": 10000,
        "model_supports_tools": True,
    })
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(agent, "_save_repository_variables", lambda gh, updates: saved.extend(updates))

    result = agent.save_autonomous_engineering_settings(
        agent.EngineeringSettings(model="small:latest", interval_hours=4, execution_mode="continuous")
    )

    assert result["saved"] is True
    assert result["model"] == "small:latest"
    assert ("AUTONOMOUS_ENGINEERING_MODEL", "small:latest") in saved



def test_ollama_status_accepts_one_token_model_context(monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"tiny:latest"}]}')
            return io.BytesIO(json.dumps({
                "details": {"family": "tiny"},
                "model_info": {"tiny.context_length": 1},
                "capabilities": ["completion", "tools"],
            }).encode("utf-8"))

    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *handlers: FakeOpener())

    result = agent._ollama_status("tiny:latest")

    assert result["model_metadata_available"] is True
    assert result["model_context_length"] == 1
    assert result["model_context_supported"] is True
    assert result["model_supports_tools"] is True


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
        assert "-ge 1" in script
        assert "Model tool calling" in script

    provisioning = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    assert ".context_length" in provisioning
    assert all("-MaximumRedirection 0" in line for line in provisioning.splitlines() if "http://127.0.0.1:11434" in line and "Invoke-RestMethod" in line)
    assert "-lt 1" in provisioning
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

def test_runner_status_uses_native_windows_process_api_without_psutil(monkeypatch):
    import subprocess
    from app import autonomous_engineering_services as services

    observed = {}
    monkeypatch.setattr(services, "psutil", None)
    monkeypatch.setattr(services.os, "name", "nt")

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="true\\n", stderr="")

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    result = services.runner_status(None)

    assert result == {"running": True, "process_detected": True}
    assert observed["command"][:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 5
