"""Workspace validation and commit preparation for one autonomous task."""

from __future__ import annotations

import re
from pathlib import Path


def snapshot(run_id: str, attempt: str, *, checked, python_executable: str) -> None:
    checked([
        python_executable, "scripts/autonomous_engineering_progress.py", "snapshot",
        "--run-id", run_id, "--run-attempt", attempt,
    ])


def task_marker(report: Path, markers) -> str:
    try:
        matches = markers.findall(report.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuntimeError("Codex did not produce its final task report.") from exc
    if len(matches) != 1:
        raise RuntimeError("Codex report must contain exactly one valid completion marker.")
    return matches[0]


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
