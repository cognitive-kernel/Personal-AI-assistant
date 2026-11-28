"""Pure readiness/status response assembly for Autonomous Engineering."""

from __future__ import annotations

from typing import Any, Callable

from .autonomous_engineering_contracts import ACTIVE_RUN_STATES


def build_status_payload(
    *,
    github: dict[str, Any],
    github_settings: dict[str, Any],
    codex: dict[str, Any],
    runner: dict[str, Any],
    ollama: dict[str, Any],
    pending_work: dict[str, Any],
    progress_storage: bool,
    model: str,
    repository: str,
    workflow: str,
    platform_name: str,
    timed_out: list[str],
    errors: list[str],
    unavailable: list[str],
    default_interval_hours: int,
    is_codex_context_variant: Callable[[str], bool],
) -> dict[str, Any]:
    """Assemble normalized probe results without performing I/O or mutating state."""
    progress_storage = progress_storage is True
    interval_hours = github_settings.get("interval_hours", default_interval_hours)
    execution_mode = github_settings.get("execution_mode", "continuous")
    paused = bool(github_settings.get("paused", False))
    checks = {
        "ollama_online": ollama["online"],
        "model_installed": ollama["model_installed"],
        "model_selectable": ollama.get("model_selectable", ollama["model_installed"] and not is_codex_context_variant(model)) is True,
        "model_is_codex_context_variant": ollama.get("model_is_codex_context_variant") is True,
        "model_context_metadata_available": ollama.get("model_metadata_available") is True,
        "model_context_supported": ollama.get("model_context_supported") is True,
        "model_tools_metadata_available": ollama.get("model_tools_metadata_available") is True,
        "model_supports_tools": ollama.get("model_supports_tools") is True,
        "codex_installed": codex["installed"],
        "runner_running": runner["running"],
        "github_cli_authenticated": github["authenticated"],
        "github_runs_available": github.get("runs_available", False),
        "progress_storage_accessible": progress_storage,
        "durable_state_available": pending_work.get("available") is True,
    }
    ready = all(
        value
        for key, value in checks.items()
        if key not in {"codex_installed", "model_is_codex_context_variant", "model_tools_metadata_available"}
    )
    active_runs = github.get("recent_runs", [])
    active = any(
        isinstance(item, dict)
        and item.get("status") in ACTIVE_RUN_STATES
        for item in active_runs
    )
    return {
        "ready": ready,
        "can_dispatch": ready and not paused and not active,
        "checks": checks,
        "pending_work": pending_work,
        "ollama": ollama,
        "codex": codex,
        "runner": runner,
        "github": github,
        "repository": repository,
        "workflow": workflow,
        "model": model,
        "models": ollama.get("models", []),
        "selectable_models": ollama.get("selectable_models", ollama.get("models", [])),
        "model_is_codex_context_variant": ollama.get("model_is_codex_context_variant", False),
        "model_context_length": ollama.get("model_context_length"),
        "model_recommended_context_length": ollama.get("model_recommended_context_length"),
        "model_context_supported": ollama.get("model_context_supported", False),
        "model_context_blocker": ollama.get("model_context_blocker"),
        "model_context_error": ollama.get("model_context_error"),
        "model_capabilities": ollama.get("model_capabilities", []),
        "model_tools_metadata_available": ollama.get("model_tools_metadata_available", False),
        "model_tools_verification_method": ollama.get("model_tools_verification_method", "unverified"),
        "model_supports_tools": ollama.get("model_supports_tools", False),
        "model_metadata_error": ollama.get("model_metadata_error"),
        "interval_hours": interval_hours,
        "execution_mode": execution_mode,
        "paused": paused,
        "progress_storage_accessible": progress_storage,
        "platform": platform_name,
        "local_only_inference": True,
        "status_probe_timeouts": timed_out,
        "status_probe_errors": errors,
        "status_probe_unavailable": unavailable,
    }
