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
        },
    ]
    assert all("command" not in item for item in evidence["command_summaries"])



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
    serialized = __import__("json").dumps(evidence)
    assert "Access is denied" not in serialized
    assert "blocked by policy" not in serialized
    assert "private-command" not in serialized



def test_codex_diagnostic_uses_bounded_reasoning_effort_without_changing_default(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "low")
    arguments = progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")
    assert ["-c", 'model_reasoning_effort="low"'] == arguments[5:7]
    assert ["-c", "sandbox_workspace_write.exclude_slash_tmp=true"] == arguments[7:9]

    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT")
    default_arguments = progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")
    assert "model_reasoning_effort" not in " ".join(default_arguments)
    assert ["-c", "sandbox_workspace_write.exclude_slash_tmp=true"] in [
        default_arguments[i:i + 2] for i in range(len(default_arguments) - 1)
    ]

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
    assert 'from = $Model' in context_script
    assert 'parameters = @{ num_ctx = $MinimumContextLength }' in context_script
    assert 'PARAMETER\\s+num_ctx' in context_script
    assert 'AUTONOMOUS_ENGINEERING_MODEL=$codexModel' in context_script
    assert '-VerifyOnly' in diagnostic
    assert '-VerifyOnly' in autonomous
    assert 'Verify persistent Ollama context after Codex smoke' in diagnostic
    assert 'Verify persistent Ollama context after autonomous Codex task' in autonomous
    assert 'steps.context_preflight.outcome == \'success\'' in diagnostic
    assert 'steps.context_preflight.outcome == \'success\'' in autonomous
    assert 'fix/automation-codex-context-smoke-20261107' in diagnostic
    assert 'CODEX_WINDOWS_SANDBOX_BACKEND: mxc' in diagnostic
    assert 'CODEX_MODEL_REASONING_EFFORT: low' in diagnostic
    assert 'run-name: "Codex Windows Sandbox Diagnostic #' in diagnostic
    cleanup_job = diagnostic.split("  sandbox-diagnostic:", 1)[0]
    assert '$branch = [uri]::EscapeDataString($env:GITHUB_REF_NAME)' in cleanup_job
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
