from app.autonomous_engineering_readiness import build_status_payload


def _payload(*, paused=False, run_status="completed"):
    return build_status_payload(
        github={
            "authenticated": True,
            "runs_available": True,
            "recent_runs": [{"status": run_status}],
        },
        github_settings={"interval_hours": 4, "execution_mode": "continuous", "paused": paused},
        codex={"installed": True},
        runner={"running": True},
        ollama={
            "online": True,
            "model_installed": True,
            "model_selectable": True,
            "model_is_codex_context_variant": False,
            "model_metadata_available": True,
            "model_context_supported": True,
            "model_tools_metadata_available": True,
            "model_supports_tools": True,
            "models": ["qwen3:14b"],
            "selectable_models": ["qwen3:14b"],
        },
        pending_work={"available": True},
        progress_storage=True,
        model="qwen3:14b",
        repository="owner/repo",
        workflow="engineering.yml",
        platform_name="test-platform",
        timed_out=[],
        errors=[],
        unavailable=[],
        default_interval_hours=24,
        is_codex_context_variant=lambda _model: False,
    )


def test_readiness_payload_is_dispatchable_when_all_required_checks_pass():
    result = _payload()

    assert result["ready"] is True
    assert result["can_dispatch"] is True
    assert result["repository"] == "owner/repo"
    assert result["model"] == "qwen3:14b"


def test_readiness_payload_blocks_dispatch_for_active_run_or_pause():
    assert _payload(run_status="in_progress")["can_dispatch"] is False
    assert _payload(paused=True)["can_dispatch"] is False


def test_readiness_payload_keeps_optional_codex_installation_out_of_ready_gate():
    result = _payload()
    result["checks"]["codex_installed"] = False
    assert result["ready"] is True
