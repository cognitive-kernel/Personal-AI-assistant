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
        "online": True, "model": model, "model_installed": True, "models": [model]
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": True, "version": "codex test"})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
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
        "online": True, "model": model, "model_installed": True, "models": [model]
    })
    monkeypatch.setattr(agent, "_codex_status", lambda: {"installed": False, "version": None})
    monkeypatch.setattr(agent, "_runner_status", lambda: {"running": True, "process_detected": True})
    monkeypatch.setattr(agent, "storage_accessible", lambda: True)
    monkeypatch.setattr(agent, "_github_snapshot", lambda: {
        "cli_installed": True, "authenticated": True, "recent_runs": [], "runs_available": True
    })

    result = agent.collect_status()

    assert result["ready"] is True
    assert result["checks"]["codex_installed"] is False


def test_status_is_not_ready_when_shared_progress_storage_is_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "_ollama_status", lambda model: {
        "online": True, "model": model, "model_installed": True, "models": [model]
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
    assert "OPENAI_API_KEY" not in page
    assert "حساب Windows اختصاصی و غیرمدیر" in agent_page
    assert "checks.github_runs_available" in agent_script
    assert 'id="runs-per-day"' in page
    assert 'id="ollama-model"' in page
    assert 'id="custom-model"' in page
    assert 'id="save-agent-settings"' in page
    assert 'id="storage-state"' in page
    assert "checks.progress_storage_accessible" in agent_script


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
    assert "پیشرفت گام‌های برنامهٔ عامل" in page
    assert "Initialize live progress before prerequisite checks" in workflow
    assert workflow.index("Snapshot accepted scorecard from trusted main") < workflow.index("Run Codex against the local Ollama model")
    assert workflow.count("snapshot --run-id") == 1
    progress_writer = (root / "scripts" / "autonomous_engineering_progress.py").read_text(encoding="utf-8")
    assert '"--local-provider", "ollama"' in progress_writer
    assert "pyproject.toml" not in workflow
    assert workflow.count("is_relative_to(Path.cwd().resolve())") == 2
    assert "WriteAllText" in runner_check
    assert "gh auth login" not in runner_check
    assert "WindowsBuiltInRole]::Administrator" in runner_check
    assert "non-administrator service account" in workflow
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
            "name": "validate",
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

    assert result["phase"] == "validation"
    assert result["phase_label"] == "اعتبارسنجی مستقل روی GitHub"
    assert result["percent"] == 94
    assert result["activity"] == "Install dependencies"


def test_live_progress_marks_all_successful_workflow_jobs_complete(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(agent, "_RUN_PROGRESS_CACHE", {"run_id": None, "at": 0.0, "value": None})
    jobs = {
        "jobs": [
            {"name": "codex", "status": "completed", "conclusion": "success", "steps": []},
            {"name": "validate", "status": "completed", "conclusion": "success", "steps": []},
            {"name": "open-draft-pr", "status": "completed", "conclusion": "success", "steps": []},
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
            {"name": "codex", "status": "completed", "conclusion": "success", "steps": []},
            {"name": "validate", "status": "queued", "conclusion": None, "steps": []},
        ]
    }
    monkeypatch.setattr(
        agent, "_run",
        lambda command, timeout=8: subprocess.CompletedProcess(command, 0, json.dumps(jobs), ""),
    )

    result = agent._enrich_progress_from_github({"run_id": "12347", "percent": 90})

    assert result["phase"] == "validation"
    assert result["percent"] == 92
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
