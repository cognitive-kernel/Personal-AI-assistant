"""Workspace validation and commit preparation for one autonomous task."""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path


def snapshot(run_id: str, attempt: str, *, checked, python_executable: str) -> None:
    checked([
        python_executable, "scripts/autonomous_engineering_progress.py", "snapshot",
        "--run-id", run_id, "--run-attempt", attempt,
    ])


class TaskReportFailureKind(str, Enum):
    NO_LAST_AGENT_MESSAGE = "no_last_agent_message"
    MISSING_TERMINAL_MARKER = "missing_terminal_marker"
    MARKER_NOT_FINAL_LINE = "marker_not_final_line"


class TaskReportError(RuntimeError):
    def __init__(self, kind: TaskReportFailureKind, message: str):
        super().__init__(message)
        self.kind = kind


_TERMINAL_MARKER = re.compile(r"(TASK_COMPLETE|QUEUE_EMPTY|TASK_BLOCKED|TASK_CONTINUE)", re.IGNORECASE)
_ANY_MARKER = re.compile(r"(?i)\b(TASK_COMPLETE|QUEUE_EMPTY|TASK_BLOCKED|TASK_CONTINUE)\b")


def task_marker(report: Path, markers=None) -> str:
    try:
        content = report.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise TaskReportError(
            TaskReportFailureKind.NO_LAST_AGENT_MESSAGE,
            "Codex produced no readable final agent message for this task.",
        ) from exc
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        raise TaskReportError(
            TaskReportFailureKind.NO_LAST_AGENT_MESSAGE,
            "Codex produced no final agent message; the output-last-message report is empty.",
        )
    terminal = _TERMINAL_MARKER.fullmatch(lines[-1])
    if terminal:
        return terminal.group(1).upper()
    if _ANY_MARKER.search(content):
        raise TaskReportError(
            TaskReportFailureKind.MARKER_NOT_FINAL_LINE,
            "Codex completion marker must be the final non-empty report line with no Markdown or punctuation.",
        )
    raise TaskReportError(
        TaskReportFailureKind.MISSING_TERMINAL_MARKER,
        "Codex final report is present but has no valid terminal completion marker.",
    )


def checkpoint_plan(path: Path) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """Read the existing ordered work queue and its completed step IDs."""
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as stream:
            data = stream.read(8193)
        if len(data) > 8192:
            return None
        payload = json.loads(data)
    except (OSError, ValueError):
        return None
    steps = payload.get("plan") if isinstance(payload, dict) and payload.get("schema_version") == 1 else None
    if not isinstance(steps, list) or not 4 <= len(steps) <= 8:
        return None
    ids: list[str] = []
    complete: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            return None
        step_id, status = step.get("id"), step.get("status")
        if not isinstance(step_id, str) or not step_id or step_id in ids:
            return None
        if status not in ("pending", "in_progress", "completed"):
            return None
        ids.append(step_id)
        if status == "completed":
            complete.add(step_id)
    return tuple(ids), frozenset(complete)


def plan_advanced(path: Path, before: tuple[tuple[str, ...], frozenset[str]] | None) -> bool:
    after = checkpoint_plan(path)
    if after is None:
        return False
    ids, done = after
    if not done or len(done) == len(ids):
        return False
    return before is None or (ids == before[0] and before[1] < done)


def context_evidence_bytes(context: str) -> int | None:
    """Keep each evidence slice small relative to the verified Context.

    UTF-8 bytes are a conservative transport budget, rather than an exact
    tokenizer. The remainder is left for protocol, tools, reasoning and output.
    """
    try:
        tokens = int(context)
    except ValueError:
        return None
    return max(1, tokens // 8) if tokens > 0 else None


def evidence_tail(text: str, limit: int | None) -> str:
    if limit is None:
        return text
    return text.encode("utf-8")[-limit:].decode("utf-8", errors="ignore")


def changed_paths(*, checked, runtime_files) -> list[str]:
    unstaged = checked(["git", "diff", "--name-only", "-z"]).split("\0")
    staged = checked(["git", "diff", "--cached", "--name-only", "-z"]).split("\0")
    untracked = checked(["git", "ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    all_paths = unstaged + staged + untracked
    return sorted({p.replace("\\", "/") for p in all_paths if p and p.replace("\\", "/") not in runtime_files})


def validate_paths(paths: list[str], *, root: Path, allowed: tuple[str, ...], protected: set[str]) -> None:
    invalid = []
    for value in paths:
        path = Path(value)
        if (
            value.startswith("/") or "\\" in value
            or any(part in {"", ".", "..", ".git"} for part in value.split("/"))
            or not value.startswith(allowed) or value in protected
            or path.is_symlink()
            or not (root / path).resolve().is_relative_to(root.resolve())
        ):
            invalid.append(value)
    if invalid:
        raise RuntimeError("Task changed protected or non-allowlisted paths: " + repr(invalid))


def prepare_commit(task_index: int, *, checked, changed_paths, validate_paths) -> str:
    validate_paths(changed_paths())
    checked(["git", "add", "-A", "--", "app", "tests", "docs", "scripts"])
    staged = checked(["git", "diff", "--cached", "--no-renames", "--name-only", "-z"]).split("\0")
    staged = [p.replace("\\", "/") for p in staged if p]
    validate_paths(staged)
    if not staged:
        raise RuntimeError("Task reported completion but produced no reviewable changes.")
    checked(["git", "diff", "--cached", "--check"])
    records = checked(["git", "diff", "--cached", "--no-renames", "--numstat", "-z"]).split("\0")
    for item in records:
        if item:
            fields = item.split("\t", 2)
            if len(fields) != 3 or fields[0] == "-" or fields[1] == "-":
                raise RuntimeError("Binary changes are not allowed in autonomous task commits.")
    checked(["git", "config", "user.name", "github-actions[bot]"])
    checked(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    checked(["git", "commit", "-m", f"chore: complete autonomous engineering task {task_index}"])
    return checked(["git", "rev-parse", "HEAD"])
