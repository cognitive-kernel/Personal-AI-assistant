from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import autonomous_engineering as agent


def test_status_readiness_requires_every_local_dependency(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_REPOSITORY", "cognitive-kernel/Personal-AI-assistant")
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model]
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True
    })

    result = agent.collect_status()

    assert result["ready"] is True
    assert all(result["checks"].values())
    assert result["local_only_inference"] is True
    assert result["workflow"] == "autonomous-codex-engineering.yml"


def test_status_is_not_ready_when_local_model_is_missing(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": False, "models": []
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True
    })

    result = agent.collect_status()

    assert result["ready"] is False
    assert result["checks"]["ollama_online"] is True
    assert result["checks"]["model_installed"] is False


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
    assert "OPENAI_API_KEY" not in page
    assert "حساب Windows اختصاصی و غیرمدیر" in agent_page
    assert "checks.github_runs_available" in agent_script
    assert 'id="runs-per-day"' in page
    assert 'id="ollama-model"' in page
    assert 'id="custom-model"' in page
    assert 'id="save-agent-settings"' in page


def test_autonomous_engineering_routes_are_registered():
    from app.main import app

    page_paths = {getattr(route, "path", None) for route in app.routes}
    api_paths = {route.path for route in agent.router.routes}

    assert "/autonomous-engineering" in page_paths
    assert "/api/autonomous-engineering/status" in api_paths
    assert "/api/autonomous-engineering/settings" in api_paths
    assert "/api/autonomous-engineering/dispatch" in api_paths
    assert "/api/autonomous-engineering/cancel" in api_paths


def test_ollama_status_bypasses_environment_proxies(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return io.BytesIO(b'{"models":[{"name":"gpt-oss:20b"}]}')

    def fake_build_opener(*handlers):
        captured["proxy_maps"] = [
            handler.proxies
            for handler in handlers
            if isinstance(handler, agent.urllib.request.ProxyHandler)
        ]
        return FakeOpener()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setattr(agent.urllib.request, "build_opener", fake_build_opener)

    result = agent._ollama_status("gpt-oss:20b")

    assert captured["proxy_maps"] == [{}]
    assert captured["url"] == "http://127.0.0.1:11434/api/tags"
    assert captured["timeout"] == 2
    assert result["online"] is True
    assert result["model_installed"] is True


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



def test_save_settings_updates_model_and_daily_frequency(monkeypatch):
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    def fake_run(command, timeout=8):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent.save_autonomous_engineering_settings(
        agent.EngineeringSettings(model="qwen3:14b", runs_per_day=4)
    )

    assert result["saved"] is True
    assert result["model"] == "qwen3:14b"
    assert result["runs_per_day"] == 4
    assert calls[0][1:4] == ["auth", "status", "--hostname"]
    assert calls[1][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_MODEL",
        "--body", "qwen3:14b", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]
    assert calls[2][1:] == [
        "variable", "set", "AUTONOMOUS_ENGINEERING_RUNS_PER_DAY",
        "--body", "4", "--repo", "cognitive-kernel/Personal-AI-assistant",
    ]


@pytest.mark.parametrize(
    ("model", "runs_per_day"),
    [("bad model name", 1), ("qwen3:14b", 5), ("../unsafe", 2)],
)
def test_save_settings_rejects_invalid_values(monkeypatch, model, runs_per_day):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")

    with pytest.raises(HTTPException) as error:
        agent.save_autonomous_engineering_settings(
            agent.EngineeringSettings(model=model, runs_per_day=runs_per_day)
        )

    assert error.value.status_code == 422


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
                '{"name":"AUTONOMOUS_ENGINEERING_RUNS_PER_DAY","value":"8"}]',
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(agent, "_run", fake_run)

    result = agent._github_snapshot()

    assert result["settings"] == {"model": "qwen3:14b", "runs_per_day": 8}


def test_progress_reader_uses_scorecard_baseline_and_date_filtered_snapshots(monkeypatch, tmp_path):
    from app import autonomous_engineering_progress as progress

    scorecard = tmp_path / "ACCEPTANCE_SCORECARD.md"
    scorecard.write_text(
        "**Snapshot:** 2026-11-01\\n"
        "**Scored baseline:** main at " + "a" * 40 + "\\n"
        "- **Engineering completion:** score = 60.47%\\n"
        "- **Overall acceptance progress:** score = 57.45%\\n",
        encoding="utf-8",
    )
    storage = tmp_path / "telemetry"
    storage.mkdir()
    (storage / "history.jsonl").write_text(
        json.dumps({
            "type": "project_snapshot", "date": "2026-11-02",
            "recorded_at": "2026-11-02T10:00:00+00:00",
            "engineering_percent": 61.0, "overall_percent": 58.0, "sha": "b" * 40,
        }) + "\\n",
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


def test_progress_reader_rejects_invalid_custom_date_range():
    from app.autonomous_engineering_progress import get_progress

    result = get_progress(period="custom", start_date="2026-11-03", end_date="2026-11-01")

    assert result["available"] is False
    assert result["error"] == "بازهٔ تاریخ معتبر نیست."


def test_autonomous_engineering_progress_route_and_compact_dashboard_controls():
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "autonomous-engineering.html").read_text(encoding="utf-8")
    script = (root / "app" / "static" / "autonomous-engineering.js").read_text(encoding="utf-8")
    api_paths = {route.path for route in agent.router.routes}

    assert "/api/autonomous-engineering/progress" in api_paths
    assert 'id="cycle-progress"' in page
    assert 'id="progress-chart"' in page
    assert 'id="progress-period"' in page
    assert 'id="progress-start"' in page
    assert 'id="progress-end"' in page
    assert 'id="progress-events"' in page
    assert 'window.setInterval(refreshProgress, 5000)' in script
    assert "createElementNS" in script
