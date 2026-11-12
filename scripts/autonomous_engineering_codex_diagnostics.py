"""Privacy-preserving classification and verification of Codex JSONL evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _diagnostic_error_text(value: Any, depth: int = 0) -> str:
    """Read only structured error fields for classification; never persist their values."""
    if depth > 3:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        allowed = {"item", "error", "message", "reason", "detail", "stderr", "stdout", "aggregated_output", "code", "kind"}
        return " ".join(
            _diagnostic_error_text(child, depth + 1)
            for key, child in value.items()
            if str(key).casefold() in allowed
        )
    if isinstance(value, list):
        return " ".join(_diagnostic_error_text(child, depth + 1) for child in value[:8])
    return ""


def _diagnostic_field_names(value: Any, depth: int = 0) -> list[str]:
    if depth > 3 or not isinstance(value, dict):
        return []
    names = set(value)
    for key in ("item", "error", "details", "cause"):
        names.update(_diagnostic_field_names(value.get(key), depth + 1))
    return sorted(str(name) for name in names)


def _sanitize_error_signature(value: Any) -> str:
    """Retain a short, path- and quote-redacted error signature, never raw command/output."""
    text = _diagnostic_error_text(value)
    if not text:
        return ""
    text = re.sub(r"(?i)\\\\[^\\/\s]+\\[^\\/\s]+(?:\\[^\\/\s]+)*", "<network-path>", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\s\"'<>]+", "<windows-path>", text)
    text = re.sub(r'"[^"]{1,240}"', '"<quoted>"', text)
    text = re.sub(r"'[^']{1,240}'", "'<quoted>'", text)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", text)
    text = re.sub(r"(?i)\b(?:api[_-]?key|token|password)\s*[:=]\s*\S+", "<redacted>", text)
    text = re.sub(r"\b[0-9a-fA-F]{16,}\b", "<id>", text)
    return " ".join(text.split())[:240]

def diagnostic_failure_category(message: Any) -> str:
    """Classify an error without retaining command text or process output."""
    text = _diagnostic_error_text(message).casefold()
    if not text.strip():
        return "no_error_detail"
    if "blocked by policy" in text or ("createprocess" in text and "rejected" in text):
        return "process_launch_rejected"
    if any(token in text for token in ("unsupportedoperation", "restricted-token", "split writable root")):
        return "unsupported_sandbox_policy"
    if any(token in text for token in ("access is denied", "access denied", "permission denied", "unauthorized")):
        return "permission_denied"
    if "sandbox" in text and any(token in text for token in ("denied", "blocked", "violation", "permission")):
        return "sandbox_denied"
    if any(token in text for token in ("timed out", "timeout", "deadline exceeded")):
        return "timeout"
    if any(token in text for token in ("not recognized as an internal or external command", "command not found", "executable not found")):
        return "executable_not_found"
    if any(token in text for token in ("cannot find the path", "path not found", "no such file or directory")):
        return "path_not_found"
    if any(token in text for token in ("invalid argument", "unknown argument", "unexpected argument", "invalid tool arguments")):
        return "invalid_tool_arguments"
    if any(token in text for token in ("failed to spawn", "failed to start process", "could not start process")):
        return "process_start_failed"
    return "unclassified_error"


def codex_smoke_evidence(
    events: list[dict[str, Any]],
    expected_command_output: str,
    expected_agent_message: str,
    required_command_fragments: list[str] | None = None,
) -> dict[str, Any]:
    started_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.started"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    completed_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    agent_messages = [
        str(event["item"].get("text", "")).strip()
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
    ]
    turn_completed = any(event.get("type") == "turn.completed" for event in events)
    error_events = [
        event for event in events
        if event.get("type") in {"turn.failed", "error"}
        or (isinstance(event.get("item"), dict) and event["item"].get("type") == "error")
    ]
    turn_failed = bool(error_events)
    error_summaries = [
        {
            "category": diagnostic_failure_category(event),
            "field_names": _diagnostic_field_names(event),
            "signature": _sanitize_error_signature(event),
        }
        for event in error_events
    ]
    error_categories = [summary["category"] for summary in error_summaries]
    required_fragments = required_command_fragments or []

    def index_by_id(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], bool]:
        indexed: dict[str, dict[str, Any]] = {}
        duplicate_or_missing_id = False
        for item in items:
            item_id = str(item.get("id", "")).strip()
            if not item_id or item_id in indexed:
                duplicate_or_missing_id = True
                continue
            indexed[item_id] = item
        return indexed, duplicate_or_missing_id

    started_by_id, duplicate_start_ids = index_by_id(started_commands)
    completed_by_id, duplicate_completion_ids = index_by_id(completed_commands)
    ids_match = (
        not duplicate_start_ids
        and not duplicate_completion_ids
        and bool(started_by_id)
        and set(started_by_id) == set(completed_by_id)
        and len(started_commands) == len(started_by_id)
        and len(completed_commands) == len(completed_by_id)
    )

    command_summaries: list[dict[str, Any]] = []
    verified_probes: list[dict[str, Any]] = []
    all_commands_succeeded = bool(completed_commands)
    all_command_payloads_match = ids_match

    for completed in completed_commands:
        item_id = str(completed.get("id", "")).strip()
        started = started_by_id.get(item_id, {})
        started_text = str(started.get("command", "")).strip()
        completed_text = str(completed.get("command", "")).strip()
        command_text = completed_text or started_text
        command_payload_matches = bool(command_text) and (
            not started_text or not completed_text or started_text == completed_text
        )
        output_text = str(completed.get("aggregated_output", ""))
        fragment_checks = {
            fragment: fragment in command_text
            for fragment in required_fragments
        }
        fragments_present = all(fragment_checks.values())
        output_marker_present = bool(expected_command_output) and expected_command_output in output_text
        succeeded = completed.get("status") == "completed" and completed.get("exit_code") == 0
        all_commands_succeeded = all_commands_succeeded and succeeded
        all_command_payloads_match = all_command_payloads_match and command_payload_matches
        summary = {
            "id": item_id,
            "status": completed.get("status"),
            "exit_code": completed.get("exit_code"),
            "start_completed_match": command_payload_matches,
            "required_fragments": fragment_checks,
            "output_marker_present": output_marker_present,
            "failure_category": None if succeeded else diagnostic_failure_category(completed),
            # Preserve only a short sanitized failure signature and field names.
            # This is essential for diagnosing real Runner failures without
            # publishing raw commands, stdout/stderr, credentials, or paths.
            "failure_field_names": [] if succeeded else _diagnostic_field_names(completed),
            "failure_signature": None if succeeded else _sanitize_error_signature(completed),
        }
        command_summaries.append(summary)
        if succeeded and command_payload_matches and fragments_present and output_marker_present:
            verified_probes.append(completed)

    final_message = agent_messages[-1] if agent_messages else ""
    exactly_one_verified_probe = len(verified_probes) == 1
    proof_command = verified_probes[0] if exactly_one_verified_probe else {}
    checks = {
        "command_events_are_paired": ids_match,
        "command_payloads_match": all_command_payloads_match,
        "all_commands_completed_successfully": all_commands_succeeded,
        "exactly_one_verified_workspace_probe": exactly_one_verified_probe,
        "turn_completed": turn_completed,
        "turn_not_failed": not turn_failed,
        "expected_agent_message": not expected_agent_message or final_message == expected_agent_message,
    }

    completed_turns = [event for event in events if event.get("type") == "turn.completed"]
    usage = completed_turns[-1].get("usage", {}) if completed_turns else {}
    event_types = [str(event.get("type", "")) for event in events]
    item_types = [
        str(event["item"].get("type", ""))
        for event in events
        if isinstance(event.get("item"), dict)
    ]
    turn_usage = {
        key: usage[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(usage, dict) and key in usage
    }

    # Evidence deliberately contains no raw command, prompt, output, or agent text.
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "command_status": proof_command.get("status"),
        "command_exit_code": proof_command.get("exit_code"),
        "command_output_marker": exactly_one_verified_probe,
        "required_command_fragments_present": exactly_one_verified_probe,
        "agent_message_matches_expected": not expected_agent_message or final_message == expected_agent_message,
        "turn_completed": turn_completed,
        "turn_failed": turn_failed,
        "error_event_count": len(error_events),
        "error_categories": error_categories,
        "error_summaries": error_summaries,
        "event_count": len(events),
        "event_types": event_types,
        "item_types": item_types,
        "command_started_count": len(started_commands),
        "command_completed_count": len(completed_commands),
        "agent_message_count": len(agent_messages),
        "turn_usage": turn_usage,
        "command_summaries": command_summaries,
    }

def write_smoke_evidence(path: str, evidence: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
