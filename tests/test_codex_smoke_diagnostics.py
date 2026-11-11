from scripts import autonomous_engineering_progress as progress


def test_codex_smoke_evidence_reports_each_command_without_logging_raw_commands():
    events = [
        {"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution",
            "command": "python -c create probe",
        }},
        {"type": "item.completed", "item": {
            "id": "cmd-1", "type": "command_execution",
            "command": "python -c create probe",
            "status": "completed", "exit_code": 0,
            "aggregated_output": "created",
        }},
        {"type": "item.started", "item": {
            "id": "cmd-2", "type": "command_execution",
            "command": "python -c read probe then unlink",
        }},
        {"type": "item.completed", "item": {
            "id": "cmd-2", "type": "command_execution",
            "command": "python -c read probe then unlink",
            "status": "completed", "exit_code": 0,
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK",
        }},
        {"type": "turn.completed"},
    ]

    evidence = progress.codex_smoke_evidence(
        events,
        "CODEX_WORKSPACE_SANDBOX_OK",
        "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        ["probe", "create", "read", "unlink"],
    )

    assert evidence["command_started_count"] == 2
    assert evidence["command_completed_count"] == 2
    assert evidence["command_summaries"] == [
        {
            "id": "cmd-1",
            "status": "completed",
            "exit_code": 0,
            "start_completed_match": True,
            "required_fragments": {
                "probe": True, "create": True, "read": False, "unlink": False,
            },
            "output_marker_present": False,
            "failure_category": None,
            "failure_field_names": [],
            "failure_signature": None,
        },
        {
            "id": "cmd-2",
            "status": "completed",
            "exit_code": 0,
            "start_completed_match": True,
            "required_fragments": {
                "probe": True, "create": False, "read": True, "unlink": True,
            },
            "output_marker_present": True,
            "failure_category": None,
            "failure_field_names": [],
            "failure_signature": None,
        },
    ]
    assert all("command" not in item for item in evidence["command_summaries"])



def test_codex_model_catalog_registers_the_selected_ollama_context():
    model = "gpt-oss:20b-codex-ctx64000"
    path = progress.write_codex_model_catalog(model, context_window=64000)
    catalog = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert len(catalog["models"]) == 1
    entry = catalog["models"][0]
    assert entry["slug"] == model
    assert entry["context_window"] == 64000
    assert entry["shell_type"] == "default"
    assert entry["input_modalities"] == ["text"]
    assert [level["effort"] for level in entry["supported_reasoning_levels"]] == [
        "low", "medium", "high"
    ]



def test_codex_smoke_evidence_classifies_failures_without_retaining_raw_output():
    events = [
        {"type": "item.started", "item": {
            "id": "failed-command", "type": "command_execution",
            "command": "python -c private-command",
        }},
        {"type": "item.completed", "item": {
            "id": "failed-command", "type": "command_execution",
            "status": "failed", "exit_code": 1,
            "aggregated_output": "Access is denied.",
        }},
        {"type": "item.completed", "item": {
            "type": "error", "error": {
                "code": "exec_command_failed",
                "message": "CreateProcess rejected: blocked by policy",
            },
        }},
        {"type": "turn.failed"},
    ]

    evidence = progress.codex_smoke_evidence(events, "MARKER", "", [])

    assert evidence["command_summaries"][0]["failure_category"] == "permission_denied"
    assert evidence["error_event_count"] == 2
    assert evidence["error_categories"] == ["process_launch_rejected", "no_error_detail"]
    assert "message" in evidence["error_summaries"][0]["field_names"]
    assert "code" in evidence["error_summaries"][0]["field_names"]
    signature = evidence["error_summaries"][0]["signature"]
    assert "CreateProcess rejected: blocked by policy" in signature
    assert "C:\\Users" not in signature
    serialized = __import__("json").dumps(evidence)
    assert "C:\\Users" not in serialized
    assert "python -c secret" not in serialized
    assert "private-command" not in serialized



def test_codex_diagnostic_uses_bounded_reasoning_effort_without_changing_default(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "low")
    arguments = progress.codex_arguments("gpt-oss:20b-codex-ctx64000", "report.md")
    overrides = [
        arguments[i + 1] for i, value in enumerate(arguments[:-1]) if value == "-c"
    ]
    assert 'model_reasoning_effort="low"' in overrides
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in overrides
    assert any(value.startswith("model_catalog_json=") for value in overrides)
    assert not any(value.startswith("model_providers.ollama.") for value in overrides)

    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT")
    default_arguments = progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")
    default_overrides = [
        default_arguments[i + 1]
        for i, value in enumerate(default_arguments[:-1])
        if value == "-c"
    ]
    assert not any(value.startswith("model_reasoning_effort=") for value in default_overrides)
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in default_overrides
    assert any(value.startswith("model_catalog_json=") for value in default_overrides)
    assert not any(value.startswith("model_providers.ollama.") for value in default_overrides)

    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "unbounded")
    with pytest.raises(ValueError, match="CODEX_MODEL_REASONING_EFFORT"):
        progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")


def test_codex_context_is_persisted_and_verified_in_both_windows_workflows():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    context_script = (root / "scripts" / "verify_ollama_context.ps1").read_text(encoding="utf-8")
    diagnostic = (root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    autonomous = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert 'model = $codexModel' in context_script
    assert 'from = $baseModel' in context_script
    assert 'parameters = @{ num_ctx = $selectedContext }' in context_script
    assert 'PARAMETER\\s+num_ctx' in context_script
    assert 'AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH=$selectedContext' in context_script
    assert context_script.count('if ($VerifyOnly) {') == 1
    assert context_script.count('OLLAMA_RUNTIME_CONTEXT_OK') == 1
    assert 'if ($allocated -ne $selectedContext)' in context_script
    assert 'if ($allocated -lt $selectedContext)' not in context_script
    assert '-lt 64000' in context_script
    assert 'AUTONOMOUS_ENGINEERING_MODEL=$codexModel' in context_script
    assert '-VerifyOnly' in diagnostic
    assert '-VerifyOnly' in autonomous
    assert 'Verify persistent Ollama context after Codex smoke' in diagnostic
    assert 'Verify persistent Ollama context after autonomous Codex task' in autonomous
    assert 'steps.context_preflight.outcome == \'success\'' in diagnostic
    assert 'steps.context_preflight.outcome == \'success\'' in autonomous
    assert "fix/codex-builtin-ollama-config-20261110" in diagnostic
    assert "fix/autonomous-task-preservation" not in diagnostic
    assert "fix/automation-codex-context-smoke-20261107" not in diagnostic
    assert 'CODEX_WINDOWS_SANDBOX_BACKEND: mxc' in diagnostic
    assert 'CODEX_MODEL_REASONING_EFFORT: low' in diagnostic
    assert "vars.AUTONOMOUS_ENGINEERING_MODEL" in diagnostic
    assert "inputs.model || 'gpt-oss:20b'" not in diagnostic
    assert 'run-name: "Codex Windows Sandbox Diagnostic #' in diagnostic
    cleanup_job = diagnostic.split("  sandbox-diagnostic:", 1)[0]
    assert '$branch = [uri]::EscapeDataString($env:GITHUB_REF_NAME)' in cleanup_job
    assert '/compare/$($run.head_sha)...$env:GITHUB_SHA' in cleanup_job
    assert '$comparison.status -ne "ahead"' in cleanup_job
    assert '$comparison.behind_by -ne 0' in cleanup_job
    assert '$comparison.ahead_by -le 0' in cleanup_job
    assert 'leaving it untouched' in cleanup_job
    sandbox_job = diagnostic.split("  sandbox-diagnostic:", 1)[1]
    assert "timeout-minutes: 30" in sandbox_job
    assert "timeout-minutes: 25" in sandbox_job
    assert "Use the exec_command tool exactly once" not in diagnostic
    assert "Use the exec_command tool exactly once" not in autonomous
    assert "single self-contained command" in diagnostic
    assert "login set to false" in diagnostic
    assert "single self-contained command" in autonomous
    assert "--expected-agent-message" not in diagnostic
    assert "CODEX_WORKSPACE_SANDBOX_OK" in diagnostic
    assert "Call exec_command once only" in diagnostic
    assert "do not retry, change the command, or print the success marker" in diagnostic
    assert "echo ok>$probeFileName && type $probeFileName && del $probeFileName" in diagnostic
    assert "--require-command-fragment \"echo\" --require-command-fragment \"type\" --require-command-fragment \"del\"" in diagnostic
    assert "python -c \"from pathlib import Path" not in diagnostic

def test_context_preflight_uses_deterministic_variant_from_selected_base_model():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify_ollama_context.ps1"
    ).read_text(encoding="utf-8")

    assert '$codexModel = "$baseModel-codex-ctx$selectedContext"' in script
    assert 'from = $baseModel' in script
    assert '$existingContext.Success' in script
    assert "if ($Model -match '(?i)-codex-ctx[0-9]+" in script
    assert "Select the original Ollama model to prevent nested variants" in script
    root = Path(__file__).resolve().parents[1]
    provision = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    app_check = (root / "scripts" / "check_autonomous_agent.ps1").read_text(encoding="utf-8")
    runner_check = (root / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    assert "internal Codex context variant" in provision
    assert "Base model selection" in app_check
    assert "Base model selection" in runner_check




def test_codex_model_catalog_rejects_context_below_64k(monkeypatch):
    import pytest
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "63999")
    with pytest.raises(ValueError, match="64000"):
        progress.write_codex_model_catalog("qwen2.5:7b-codex-ctx63999")


def test_codex_model_catalog_uses_selected_context_from_environment(monkeypatch):
    import json
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    path = progress.write_codex_model_catalog("qwen2.5:7b-codex-ctx64000")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["models"][0]["context_window"] == 64000


def test_settings_expose_visible_installed_model_selectors_and_manual_context_inputs():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    for profile in ("chat", "learning", "programming"):
        assert f'id="{profile}-model-installed"' in html
        assert f'id="{profile}-model"' in html
        assert f'id="{profile}-ctx" type="number"' in html
        assert f'id="{profile}-model-installed"' in html
    assert 'const result=await api("/api/models")' in html
    assert 'installedSelect?.addEventListener("change"' in html
