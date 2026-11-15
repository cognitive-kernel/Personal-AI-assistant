"""Bounded inventory and conservative cleanup for autonomous-engineering artifacts."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .autonomous_engineering_state import execution_state_directory

_RETENTION_SECONDS = 24 * 60 * 60
_MAX_ITEMS = 500
_RUNNER_TEMP_PREFIXES = (
    "codex-report-",
    "codex-sandbox-",
)
_RUNNER_TEMP_EXACT = {"codex-windows-sandbox-smoke.md", "capture_state.py"}
_STATE_TEMP_SUFFIXES = (".tmp",)


def _safe_file(path: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        return resolved != resolved_root and resolved.is_relative_to(resolved_root) and path.is_file() and not path.is_symlink()
    except (OSError, RuntimeError, ValueError):
        return False


def _entry(path: Path, root: Path) -> dict[str, Any] | None:
    if not _safe_file(path, root):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "name": path.name,
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
        "age_seconds": max(0, int(time.time() - stat.st_mtime)),
    }


def inspect_storage() -> dict[str, Any]:
    """Inspect only app-owned state files and known Codex artifacts; never traverse arbitrary temp trees."""
    state_root = execution_state_directory()
    candidates: list[tuple[Path, Path, str]] = []
    try:
        if state_root.is_dir():
            for path in state_root.iterdir():
                if path.is_file() and path.name.endswith(_STATE_TEMP_SUFFIXES):
                    candidates.append((path, state_root, "state_temporary"))
    except OSError:
        pass

    runner_temp_value = os.environ.get("RUNNER_TEMP")
    runner_temp = Path(runner_temp_value) if runner_temp_value else None
    if runner_temp and runner_temp.is_dir():
        try:
            for path in runner_temp.iterdir():
                if not path.is_file():
                    continue
                if path.name in _RUNNER_TEMP_EXACT or path.name.startswith(_RUNNER_TEMP_PREFIXES):
                    candidates.append((path, runner_temp, "runner_temporary"))
        except OSError:
            pass

    files: list[dict[str, Any]] = []
    total = 0
    for path, root, category in candidates[:_MAX_ITEMS]:
        item = _entry(path, root)
        if item is None:
            continue
        item["category"] = category
        files.append(item)
        total += item["size_bytes"]
    files.sort(key=lambda item: item["size_bytes"], reverse=True)
    return {
        "available": True,
        "state_directory": str(state_root),
        "runner_temp_directory": str(runner_temp) if runner_temp else None,
        "files": files[:100],
        "file_count": len(files),
        "total_bytes": total,
        "truncated": len(candidates) > _MAX_ITEMS,
        "retention_hours": 24,
        "cleanup_policy": "Only app-owned *.tmp files and known Codex temporary artifacts older than 24 hours are eligible. Active runs block cleanup.",
    }


def cleanup_storage(*, active_run: bool) -> dict[str, Any]:
    """Delete only known stale temporary artifacts, and refuse while any workflow is active."""
    if active_run:
        return {"deleted": [], "deleted_count": 0, "blocked": True, "message": "اجرای فعال وجود دارد؛ پاک‌سازی تا پایان اجرا انجام نشد."}

    state_root = execution_state_directory()
    roots: list[tuple[Path, str]] = [(state_root, "state_temporary")]
    runner_temp_value = os.environ.get("RUNNER_TEMP")
    if runner_temp_value:
        roots.append((Path(runner_temp_value), "runner_temporary"))

    cutoff = time.time() - _RETENTION_SECONDS
    deleted: list[str] = []
    failures: list[str] = []
    for root, category in roots:
        try:
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if category == "state_temporary":
                    eligible = path.name.endswith(_STATE_TEMP_SUFFIXES)
                else:
                    eligible = (
                        path.name in _RUNNER_TEMP_EXACT
                        or path.name.startswith(_RUNNER_TEMP_PREFIXES)
                    )
                if not eligible or not _safe_file(path, root):
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                    path.unlink()
                    deleted.append(str(path))
                except OSError:
                    failures.append(path.name)
        except OSError:
            failures.append(str(root))
    return {
        "deleted": deleted,
        "deleted_count": len(deleted),
        "blocked": False,
        "failures": failures,
        "message": f"{len(deleted)} فایل موقت قدیمی حذف شد؛ فایل‌های داده و checkpoint دست‌نخورده ماندند.",
    }
