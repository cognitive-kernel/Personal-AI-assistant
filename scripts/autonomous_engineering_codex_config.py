"""Codex CLI model catalog, context validation, and argument construction."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from app.ollama_context import MAX_CODEX_CONTEXT_TOKENS, MIN_CONTEXT_TOKENS


ROOT = Path(__file__).resolve().parents[1]


def codex_command(executable: str, arguments: list[str]) -> list[str]:
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        return [
            os.getenv("COMSPEC", "cmd.exe"), "/d", "/c", "call",
            executable, *arguments,
        ]
    return [executable, *arguments]


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
    if not MIN_CONTEXT_TOKENS <= context_window <= MAX_CODEX_CONTEXT_TOKENS:
        raise ValueError(
            f"Codex context window must be between {MIN_CONTEXT_TOKENS} and {MAX_CODEX_CONTEXT_TOKENS} tokens."
        )
    return context_window


def write_codex_model_catalog(model: str, context_window: int | None = None) -> Path:
    """Provide Codex metadata using the exact context selected and verified by Ollama."""
    if context_window is None:
        context_window = _verified_codex_context_window()
    if (
        isinstance(context_window, bool)
        or not isinstance(context_window, int)
        or not MIN_CONTEXT_TOKENS <= context_window <= MAX_CODEX_CONTEXT_TOKENS
    ):
        raise ValueError(
            f"Codex context window must be between {MIN_CONTEXT_TOKENS} and {MAX_CODEX_CONTEXT_TOKENS} tokens."
        )
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
        "model_providers.ollama-local.stream_idle_timeout_ms=900000",
        "model_providers.ollama-local.stream_max_retries=10",
        "model_providers.ollama-local.request_max_retries=4",
    ]
    if reasoning_effort:
        config_overrides.insert(0, f'model_reasoning_effort="{reasoning_effort}"')
    arguments[5:5] = [part for override in config_overrides for part in ("-c", override)]
    return arguments
