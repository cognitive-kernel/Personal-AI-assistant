"""Redaction and root-cause extraction for Autonomous Engineering workflow logs."""

from __future__ import annotations

import re
from typing import Any, Callable


def sanitize_workflow_log(value: str, *, max_chars: int = 120_000) -> str:
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)\S+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)(authorization\s*:\s*basic\s+)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    value = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"""(?i)\b[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|gh[_-]?token)[\w-]*(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s]+)""",
        r"[REDACTED]\1[REDACTED]",
        value,
    )
    if len(value) > max_chars:
        value = "[ابتدای گزارش به‌دلیل محدودیت حجم حذف شده است.]\n" + value[-max_chars:]
    return value


def workflow_failure_summary(
    value: str,
    failures: list[dict[str, Any]],
    *,
    sanitize_log: Callable[[str], str] | None = None,
) -> str | None:
    """Prefer the controller's root-cause line over generic shell exit messages."""
    sanitize = sanitize_log or sanitize_workflow_log
    lines = [line.strip() for line in sanitize(value).splitlines() if line.strip()]
    if any("Warning: no last agent message" in line for line in lines):
        return (
            "Codex did not emit a final agent message; the output-last-message report was empty, "
            "so the controller could not validate a completion marker. Preserve the durable "
            "task/checkpoint and inspect the Codex turn's terminal events before retrying."
        )
    preferred = []
    for line in lines:
        marker = "Autonomous engineering cycle failed:"
        if marker in line:
            preferred.append(line.split(marker, 1)[1].strip())
        elif "Codex reported task " in line and " as blocked" in line:
            preferred.append(line)
    if preferred:
        return preferred[-1][:800]
    actionable = [
        line for line in lines
        if (
            ("##[error]" in line or re.search(r"(?i)\b(error|exception|traceback):", line))
            and "Process completed with exit code" not in line
        )
    ]
    if actionable:
        return actionable[-1][:800]
    if failures:
        first = failures[0]
        failed_steps = first.get("failed_steps") if isinstance(first.get("failed_steps"), list) else []
        step = failed_steps[0].get("name") if failed_steps and isinstance(failed_steps[0], dict) else None
        return (f"{first.get('name', 'Job')} failed" + (f" at {step}" if step else ""))[:800]
    return None
