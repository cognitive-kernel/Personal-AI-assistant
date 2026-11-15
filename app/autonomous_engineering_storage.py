"""Bounded inventory and conservative cleanup for autonomous-engineering artifacts."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import psutil

from .autonomous_engineering_state import execution_state_directory

_RETENTION_SECONDS = 24 * 60 * 60
_DIAGNOSTIC_RETENTION_SECONDS = 30 * 24 * 60 * 60
_MAX_ITEMS = 500
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


def _runner_temp_directories() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("AUTONOMOUS_ENGINEERING_RUNNER_TEMP")
    if configured:
        roots.append(Path(os.path.expandvars(configured)))
    runner_temp_env = os.environ.get("RUNNER_TEMP")
    if not configured and runner_temp_env:
        candidate_temp = Path(os.path.expandvars(runner_temp_env))
        if candidate_temp.name.casefold() == "_temp" and candidate_temp.parent.name.casefold() == "_work":
            roots.append(candidate_temp)
    if not configured and not roots and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
                configured, _ = winreg.QueryValueEx(key, "AUTONOMOUS_ENGINEERING_RUNNER_TEMP")
        except (OSError, ImportError):
            configured = None
        if configured and not roots:
            roots.append(Path(os.path.expandvars(configured)))
    try:
        for process in psutil.process_iter(["name", "exe", "cmdline"]):
            try:
                name = str(process.info.get("name") or "").casefold()
                command = process.info.get("cmdline") or []
                executable = process.info.get("exe")
                if "runner.listener" not in name and not any("runner.listener" in str(part).casefold() for part in command):
                    continue
                candidate = Path(executable) if executable else next(
                    (Path(part) for part in command if "runner.listener" in str(part).casefold() and Path(part).is_absolute()),
                    None,
                )
                if candidate is None:
                    continue
                runner_root = candidate.parent.parent if candidate.parent.name.casefold() == "bin" else candidate.parent
                roots.append(runner_root / "_work" / "_temp")
            except (OSError, psutil.Error, TypeError, ValueError):
                continue
    except (OSError, psutil.Error):
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(str(root)))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _runner_worker_active() -> bool:
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = str(process.info.get("name") or "").casefold()
                if "runner.worker" in name:
                    return True
            except (OSError, psutil.Error):
                continue
    except (OSError, psutil.Error):
        return True
    return False


def inspect_storage() -> dict[str, Any]:
    """Inventory state files, the dedicated Runner temp directory, and Runner diagnostics without recursive traversal."""
    state_root = execution_state_directory()
    candidates: list[tuple[Path, Path, str]] = []
    access_errors: list[dict[str, str]] = []
    try:
        if state_root.is_dir():
            for path in state_root.iterdir():
                if path.is_file():
                    category = "state_temporary" if path.name.endswith(_STATE_TEMP_SUFFIXES) else "state_data_read_only"
                    candidates.append((path, state_root, category))
    except OSError as exc:
        access_errors.append({"path": str(state_root), "error": type(exc).__name__})

    runner_temp_dirs = _runner_temp_directories()
    if not runner_temp_dirs:
        access_errors.append({"path": "Runner temp", "error": "runner_temp_path_not_configured_or_detected"})
    for runner_temp in runner_temp_dirs:
        if runner_temp.is_dir():
            try:
                for path in runner_temp.iterdir():
                    if path.is_file():
                        candidates.append((path, runner_temp, "runner_temporary"))
            except OSError as exc:
                access_errors.append({"path": str(runner_temp), "error": type(exc).__name__})
        else:
            access_errors.append({"path": str(runner_temp), "error": "directory_missing_or_inaccessible"})
    runner_roots = [path.parent.parent for path in runner_temp_dirs if path.name.casefold() == "_temp" and path.parent.name.casefold() == "_work"]
    for runner_root in runner_roots:
        diagnostic_dir = runner_root / "_diag"
        try:
            for path in diagnostic_dir.iterdir():
                if path.is_file() and path.suffix.casefold() in {".log", ".txt"}:
                    candidates.append((path, diagnostic_dir, "runner_diagnostic"))
        except OSError as exc:
            access_errors.append({"path": str(diagnostic_dir), "error": type(exc).__name__})

    files: list[dict[str, Any]] = []
    total = 0
    for path, root, category in candidates[:_MAX_ITEMS]:
        item = _entry(path, root)
        if item is None:
            continue
        item["category"] = category
        if category == "runner_diagnostic":
            item["cleanup_eligible"] = item["age_seconds"] >= _DIAGNOSTIC_RETENTION_SECONDS
        elif category in {"state_temporary", "runner_temporary"}:
            item["cleanup_eligible"] = item["age_seconds"] >= _RETENTION_SECONDS
        else:
            item["cleanup_eligible"] = False
        files.append(item)
        total += item["size_bytes"]
    files.sort(key=lambda item: item["size_bytes"], reverse=True)
    return {
        "available": True,
        "state_directory": str(state_root),
        "runner_temp_directories": [str(path) for path in runner_temp_dirs],
        "access_errors": access_errors,
        "files": files[:100],
        "file_count": len(files),
        "total_bytes": total,
        "truncated": len(candidates) > _MAX_ITEMS,
        "retention_hours": 24,
        "diagnostic_retention_days": 30,
        "cleanup_policy": "Runner temp files and state-root *.tmp files older than 24 hours; Runner _diag .log/.txt files older than 30 days. Active runs block cleanup.",
    }


def cleanup_storage(*, active_run: bool) -> dict[str, Any]:
    """Delete expired files only from dedicated Runner temp/state temp and old Runner diagnostic logs."""
    if active_run or _runner_worker_active():
        return {"deleted": [], "deleted_count": 0, "blocked": True, "message": "اجرای فعال یا فرایند Runner.Worker شناسایی شد؛ پاک‌سازی تا پایان اجرای همهٔ Jobها انجام نشد."}

    state_root = execution_state_directory()
    roots: list[tuple[Path, str]] = [(state_root, "state_temporary")]
    runner_temp_dirs = _runner_temp_directories()
    roots.extend((path, "runner_temporary") for path in runner_temp_dirs)
    roots.extend((path.parent.parent / "_diag", "runner_diagnostic") for path in runner_temp_dirs if path.name.casefold() == "_temp" and path.parent.name.casefold() == "_work")

    deleted: list[str] = []
    failures: list[str] = []
    for root, category in roots:
        try:
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if category == "state_temporary":
                    eligible = path.name.endswith(_STATE_TEMP_SUFFIXES)
                    retention = _RETENTION_SECONDS
                elif category == "runner_temporary":
                    eligible = True
                    retention = _RETENTION_SECONDS
                else:
                    eligible = path.is_file() and path.suffix.casefold() in {".log", ".txt"}
                    retention = _DIAGNOSTIC_RETENTION_SECONDS
                if not eligible or not _safe_file(path, root):
                    continue
                try:
                    if path.stat().st_mtime >= time.time() - retention:
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
        "message": f"{len(deleted)} فایل موقت یا گزارش قدیمی حذف شد؛ فایل‌های داده و checkpoint دست‌نخورده ماندند.",
    }
