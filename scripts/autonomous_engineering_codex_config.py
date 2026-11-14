"""Codex CLI model catalog, context validation, and bounded runtime construction."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    from .autonomous_engineering_codex_runner import capture_parent_chain
except ImportError:
    from autonomous_engineering_codex_runner import capture_parent_chain


ROOT = Path(__file__).resolve().parents[1]
CODEX_SMOKE_TIMEOUT_SECONDS = 300
CODEX_TASK_TIMEOUT_SECONDS = 1800


def codex_command(
    executable: str,
    arguments: list[str],
    *,
    timeout_seconds: int | None = None,
) -> list[str]:
    """Run Codex through an owner-aware supervisor, preserving the native CLI contract."""
    if timeout_seconds is None:
        try:
            report_index = arguments.index("--output-last-message")
            report_name = Path(arguments[report_index + 1]).name
        except (ValueError, IndexError):
            report_name = ""
        timeout_seconds = CODEX_SMOKE_TIMEOUT_SECONDS if report_name.startswith("codex-sandbox-") else CODEX_TASK_TIMEOUT_SECONDS
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("Codex timeout must be a positive integer.")
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        native_command = [
            os.getenv("COMSPEC", "cmd.exe"), "/d", "/c", "call",
            executable, *arguments,
        ]
    else:
        native_command = [executable, *arguments]
    runner = Path(__file__).with_name("autonomous_engineering_codex_runner.py")
    return [
        sys.executable, str(runner),
        "--timeout-seconds", str(timeout_seconds),
        "--parent-chain", json.dumps(capture_parent_chain(), separators=(",", ":")),
        "--", *native_command,
    ]


def _verified_codex_context_window() -> int:
    raw_context = os.getenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "").strip()
    if not raw_context:
        raise ValueError(
            "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH is missing; the verified Ollama context preflight must run before Codex."
        )
    try:
        context_window = int(raw_context)
    except ValueError:
        raise ValueError("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be an integer.") from None
    if context_window <= 0:
        raise ValueError("Codex context window must be a positive integer selected by the verified Ollama preflight.")
    return context_window


def write_codex_model_catalog(model: str, context_window: int | None = None) -> Path:
    """Provide Codex metadata using the exact context selected and verified by Ollama."""
    if context_window is None:
        context_window = _verified_codex_context_window()
    if isinstance(context_window, bool) or not isinstance(context_window, int) or context_window <= 0:
        raise ValueError("Codex context window must be a positive integer selected by the verified Ollama preflight.")
    safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    catalog_path = Path(tempfile.gettempdir()) / f"codex-model-catalog-{os.getpid()}-{safe_model}.json"
    catalog = {
        "models": [
            {
                "slug": model,
                "display_name": model,
                "context_window": context_window,
                "shell_type": "default",
                "visibility": "list",
                "supported_in_api": True,
                "priority": 0,
                "truncation_policy": {"mode": "bytes", "limit": 10000},
                "input_modalities": ["text"],
                "base_instructions": "",
                "support_verbosity": True,
                "default_verbosity": "low",
                "supports_parallel_tool_calls": False,
                "supports_reasoning_summaries": False,
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low reasoning effort"},
                    {"effort": "medium", "description": "Medium reasoning effort"},
                    {"effort": "high", "description": "High reasoning effort"},
                ],
                "experimental_supported_tools": [],
            }
        ]
    }
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog_path


def codex_arguments(model: str, report: str) -> list[str]:
    # Pin the provider and the Windows sandbox backend explicitly. The native
    # unelevated backend in affected Codex CLI releases can reject exec_command
    # before process creation or refuse workspace-write root layouts. The
    # workflow selects a backend only after a real in-workspace tool smoke test.
    backend = os.getenv("CODEX_WINDOWS_SANDBOX_BACKEND", "mxc").strip().casefold()
    if backend not in {"mxc", "elevated"}:
        raise ValueError("CODEX_WINDOWS_SANDBOX_BACKEND must be 'mxc' or 'elevated'.")
    reasoning_effort = os.getenv("CODEX_MODEL_REASONING_EFFORT", "").strip().casefold()
    if reasoning_effort and reasoning_effort not in {"low", "medium", "high"}:
        raise ValueError("CODEX_MODEL_REASONING_EFFORT must be 'low', 'medium', or 'high'.")
    context_window = _verified_codex_context_window()
    catalog_path = write_codex_model_catalog(model, context_window=context_window)
    print(f"Codex model catalog metadata: model={model}, context_window={context_window}")
    is_smoke = Path(report).name.startswith("codex-sandbox-")
    arguments = [
        "exec", "-c", 'model_provider="ollama-local"',
        "-c", f'windows.sandbox="{backend}"',
        "--sandbox", "workspace-write",
        "--model", model, "--cd", str(ROOT),
        "--output-last-message", report, "--json", "-",
    ]
    # On Windows, the legacy workspace-write policy can project the Unix
    # /tmp special root onto a drive-root path and create an unsupported split
    # writable-root set for exec_command. Exclude that unintended root while
    # preserving the workspace and the normal per-user temp directory.
    config_overrides = [
        "sandbox_workspace_write.exclude_slash_tmp=true",
        f"model_catalog_json={json.dumps(str(catalog_path))}",
        'model_providers.ollama-local.name="Ollama local"',
        'model_providers.ollama-local.base_url="http://127.0.0.1:11434/v1/"',
        'model_providers.ollama-local.wire_api="responses"',
        "model_providers.ollama-local.requires_openai_auth=false",
        "model_providers.ollama-local.stream_idle_timeout_ms=120000" if is_smoke else "model_providers.ollama-local.stream_idle_timeout_ms=300000",
        "model_providers.ollama-local.stream_max_retries=0" if is_smoke else "model_providers.ollama-local.stream_max_retries=2",
        "model_providers.ollama-local.request_max_retries=0" if is_smoke else "model_providers.ollama-local.request_max_retries=1",
    ]
    if reasoning_effort:
        config_overrides.insert(0, f'model_reasoning_effort="{reasoning_effort}"')
    arguments[5:5] = [part for override in config_overrides for part in ("-c", override)]
    return arguments
