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
            "required_fragments": {
                "probe": True, "create": True, "read": False, "unlink": False,
            },
            "output_marker_present": False,
        },
        {
            "id": "cmd-2",
            "status": "completed",
            "exit_code": 0,
            "required_fragments": {
                "probe": True, "create": False, "read": True, "unlink": True,
            },
            "output_marker_present": True,
        },
    ]
    assert all("command" not in item for item in evidence["command_summaries"])
