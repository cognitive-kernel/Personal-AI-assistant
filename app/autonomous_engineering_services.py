"""Injected service layer for Autonomous Engineering status and repository controls."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

from fastapi import HTTPException

from .ollama_context import OllamaModelMetadataError


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep local Ollama status checks on the loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ollama_status(model: str, *, build_opener, min_context_tokens, maximum_context_fn, supports_tool_calling_fn, is_context_variant) -> dict[str, Any]:
    empty = {
        "online": False,
        "model": model,
        "model_installed": False,
        "model_selectable": False,
        "model_is_codex_context_variant": is_context_variant(model),
        "models": [],
        "selectable_models": [],
        "model_context_length": None,
        "model_capabilities": [],
        "model_supports_tools": False,
        "model_metadata_available": False,
        "model_context_supported": False,
        "model_context_error": f"Model must expose at least {min_context_tokens} valid runtime context tokens.",
        "model_metadata_error": "Ollama metadata is unavailable.",
    }
    # Local Ollama metadata must never be sent through an environment proxy.
    opener = build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirectHandler(),
    )
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/tags",
        headers={"Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=3) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Ollama /api/tags response exceeded the safety limit.")
        payload = json.loads(body.decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = sorted({
            str(item.get("name", ""))
            for item in models
            if isinstance(item, dict) and item.get("name")
        })
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        return empty

    installed = bool(model) and model in names
    selected_is_context_variant = is_context_variant(model)
    selectable_names = [name for name in names if not is_context_variant(name)]
    result = {
        **empty,
        "online": True,
        "model_installed": installed,
        "model_selectable": installed and not selected_is_context_variant,
        "model_is_codex_context_variant": selected_is_context_variant,
        "models": names,
        "selectable_models": selectable_names,
        "model_metadata_error": (
            "The saved model is an internal Codex context variant; select its original Ollama model."
            if selected_is_context_variant else
            None if installed else
            "No Ollama model is saved in repository settings." if not model else
            "The saved model is not installed locally."
        ),
    }
    if not installed or selected_is_context_variant:
        return result

    show_request = urllib.request.Request(
        "http://127.0.0.1:11434/api/show",
        data=json.dumps({"model": model, "verbose": False}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(show_request, timeout=5) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Ollama /api/show response exceeded the safety limit.")
        details_payload = json.loads(body.decode("utf-8"))
        if not isinstance(details_payload, dict):
            raise ValueError("Ollama /api/show returned an invalid response.")
        details = details_payload.get("details") if isinstance(details_payload.get("details"), dict) else {}
        model_info = details_payload.get("model_info") if isinstance(details_payload.get("model_info"), dict) else {}
        architecture = details.get("family")
        try:
            context_length = maximum_context_fn(model_info, architecture)
        except OllamaModelMetadataError:
            context_length = None
        raw_capabilities = details_payload.get("capabilities")
        capabilities = sorted({
            value for value in raw_capabilities
            if isinstance(value, str) and value
        }) if isinstance(raw_capabilities, list) else []
        metadata_available = context_length is not None
        context_supported = metadata_available and context_length >= min_context_tokens
        result.update({
            "model_context_length": context_length,
            "model_capabilities": capabilities,
            "model_supports_tools": supports_tool_calling_fn(capabilities),
            "model_metadata_available": metadata_available,
            "model_context_supported": context_supported,
            "model_context_error": (
                None if context_supported else
                f"Selected model context is below the minimum supported runtime window of {min_context_tokens} tokens."
                if metadata_available else
                "Selected model context metadata is unavailable."
            ),
            "model_metadata_error": (
                None if metadata_available else
                "Ollama did not publish a valid architecture-specific context_length."
            ),
        })
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        result["model_metadata_error"] = f"Could not verify selected-model metadata: {type(exc).__name__}"
    return result

def codex_status(which, run_command) -> dict[str, Any]:
    executable = which("codex")
    if not executable:
        return {"installed": False, "version": None}
    result = run_command([executable, "--version"], timeout=5)
    version = (result.stdout or "").strip()[:120] if result and result.returncode == 0 else None
    return {"installed": bool(version), "version": version}


def runner_status(process_iter=None) -> dict[str, Any]:
    """Detect the GitHub Runner with psutil when present, otherwise native OS APIs."""
    if process_iter is None and psutil is not None:
        process_iter = psutil.process_iter
    found = False
    if process_iter is not None:
        errors = (OSError,) + ((psutil.Error,) if psutil is not None else ())
        try:
            for process in process_iter(["name", "cmdline"]):
                try:
                    name = (process.info.get("name") or "").casefold()
                    command = " ".join(process.info.get("cmdline") or []).casefold()
                    if "runner.listener" in name or "runner.listener" in command:
                        found = True
                        break
                except errors:
                    continue
        except errors:
            found = False
    elif os.name == "nt":
        script = r"""
$ErrorActionPreference = 'Stop'
$found = Get-CimInstance -ClassName Win32_Process | Where-Object {
    $_.Name -match 'Runner\.Listener' -or $_.CommandLine -match 'Runner\.Listener'
} | Select-Object -First 1
if ($null -ne $found) { 'true' } else { 'false' }
"""
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=5, check=False, shell=False,
            )
            found = bool(result.returncode == 0 and result.stdout.strip().casefold() == "true")
        except (OSError, subprocess.TimeoutExpired):
            found = False
    elif Path("/proc").is_dir():
        for command_path in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                command = command_path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").casefold()
                if "runner.listener" in command:
                    found = True
                    break
            except OSError:
                continue
    return {"running": found, "process_detected": found}


def github_snapshot(*, run_command, which, repository, workflow, default_interval_hours, allowed_interval_hours, model_pattern) -> dict[str, Any]:
    gh = which("gh")
    if not gh:
        return {
            "cli_installed": False,
            "authenticated": False,
            "recent_runs": [],
            "runs_available": False,
        }
    auth = run_command([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    authenticated = bool(auth and auth.returncode == 0)
    recent_runs: list[dict[str, Any]] = []
    runs_available = False
    settings = {"model": "", "interval_hours": default_interval_hours, "execution_mode": "continuous", "paused": False}
    if authenticated:
        result = run_command(
            [
                gh, "run", "list", "--repo", repository, "--workflow", workflow,
                "--limit", "30", "--json",
                "databaseId,status,conclusion,createdAt,url,headBranch",
            ],
            timeout=12,
        )
        if result and result.returncode == 0:
            try:
                parsed = json.loads(result.stdout)
                if isinstance(parsed, list):
                    mapped_runs = [
                        {
                            "id": item.get("databaseId"),
                            "status": item.get("status"),
                            "conclusion": item.get("conclusion"),
                            "created_at": item.get("createdAt"),
                            "url": item.get("url"),
                            "branch": item.get("headBranch"),
                        }
                        for item in parsed
                        if isinstance(item, dict)
                    ]
                    recent_runs = mapped_runs[:5]
                    visible_ids = {item["id"] for item in recent_runs}
                    active_states = {"queued", "in_progress", "waiting", "requested", "pending"}
                    recent_runs.extend(
                        item for item in mapped_runs[5:]
                        if item.get("status") in active_states and item.get("id") not in visible_ids
                    )
                    runs_available = True
            except ValueError:
                recent_runs = []
        variables = run_command(
            [gh, "variable", "list", "--repo", repository, "--json", "name,value"],
            timeout=12,
        )
        if variables and variables.returncode == 0:
            try:
                values = json.loads(variables.stdout)
                if isinstance(values, list):
                    configured = {
                        str(item.get("name")): str(item.get("value", ""))
                        for item in values if isinstance(item, dict)
                    }
                    candidate_model = configured.get("AUTONOMOUS_ENGINEERING_MODEL", "")
                    candidate_interval = configured.get("AUTONOMOUS_ENGINEERING_INTERVAL_HOURS", "")
                    candidate_mode = configured.get("AUTONOMOUS_ENGINEERING_MODE", "continuous")
                    settings["paused"] = configured.get("AUTONOMOUS_ENGINEERING_PAUSED", "false").lower() == "true"
                    if model_pattern.fullmatch(candidate_model):
                        settings["model"] = candidate_model
                    if candidate_interval.isdigit() and int(candidate_interval) in allowed_interval_hours:
                        settings["interval_hours"] = int(candidate_interval)
                    if candidate_mode in {"scheduled", "continuous"}:
                        settings["execution_mode"] = candidate_mode
            except (ValueError, TypeError):
                pass
    return {
        "cli_installed": True,
        "authenticated": authenticated,
        "recent_runs": recent_runs,
        "runs_available": runs_available,
        "settings": settings,
    }


def save_repository_variables(gh: str, updates: tuple[tuple[str, str], ...], *, run_command, repository) -> None:
    """Apply repository-variable updates as a rollback-protected transaction."""
    listing = run_command(
        [gh, "variable", "list", "--repo", repository, "--json", "name,value"],
        timeout=12,
    )
    if not listing or listing.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail="Could not snapshot existing GitHub repository settings; no settings were changed.",
        )
    try:
        current_values = json.loads(listing.stdout)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub returned invalid repository-variable state; no settings were changed.",
        ) from exc
    if not isinstance(current_values, list):
        raise HTTPException(
            status_code=502,
            detail="GitHub returned an invalid repository-variable list; no settings were changed.",
        )
    previous = {
        str(item.get("name")): str(item.get("value", ""))
        for item in current_values
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    applied: list[str] = []
    for name, value in updates:
        result = run_command(
            [gh, "variable", "set", name, "--body", value, "--repo", repository],
            timeout=15,
        )
        if result and result.returncode == 0:
            applied.append(name)
            continue

        rollback_errors: list[str] = []
        # Include the failed write itself: a client timeout/non-zero exit can
        # occur after GitHub accepted the update, so its remote state is uncertain.
        for changed_name in reversed([*applied, name]):
            if changed_name in previous:
                rollback = run_command(
                    [gh, "variable", "set", changed_name, "--body", previous[changed_name], "--repo", repository],
                    timeout=15,
                )
            else:
                rollback = run_command(
                    [gh, "variable", "delete", changed_name, "--repo", repository],
                    timeout=15,
                )
            if not rollback or rollback.returncode != 0:
                verification = run_command(
                    [gh, "variable", "list", "--repo", repository, "--json", "name,value"],
                    timeout=12,
                )
                try:
                    observed = json.loads(verification.stdout) if verification and verification.returncode == 0 else None
                except (TypeError, ValueError):
                    observed = None
                observed_values = {
                    str(item.get("name")): str(item.get("value", ""))
                    for item in observed
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                } if isinstance(observed, list) else None
                restored = (
                    observed_values is not None
                    and (
                        observed_values.get(changed_name) == previous[changed_name]
                        if changed_name in previous
                        else changed_name not in observed_values
                    )
                )
                if not restored:
                    rollback_errors.append(changed_name)
        if rollback_errors:
            detail = (
                f"Saving {name} failed and rollback could not restore: "
                + ", ".join(rollback_errors)
                + ". Inspect the repository Actions variables before retrying."
            )
        else:
            detail = f"Saving {name} failed; all earlier changes were rolled back."
        raise HTTPException(status_code=502, detail=detail)
