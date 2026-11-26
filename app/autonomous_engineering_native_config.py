"""Explicit service-free state selection.

No automatic fallback and no implicit migration: the same app/Runner root must
be selected by both processes. Legacy roots are checked read-only.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable


def native_state_enabled() -> bool:
    return os.getenv("AUTONOMOUS_ENGINEERING_STATE_BACKEND", "").strip().casefold() == "native"


def _absolute(value: str) -> Path:
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        raise ValueError("Native engineering state must use an absolute path.")
    return path


def _existing(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _same(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def resolve_native_state_directory(
    *, is_windows: bool, machine_state: Callable[[], str],
) -> Path:
    from platformdirs import site_data_path, user_data_path

    user_root = Path(user_data_path("PersonalAIAssistant", appauthor=False)) / "autonomous-engineering"
    configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    selected = _absolute(configured) if configured else user_root
    if not selected.is_absolute():
        raise RuntimeError("OS user-data directory is not absolute.")

    authoritative = None
    if is_windows:
        raw = machine_state()
        if raw:
            authoritative = _absolute(raw)
    else:
        from .autonomous_engineering_state_broker_config import configured_state_directory
        authoritative = configured_state_directory()

    # A provisioned service root must never be silently bypassed. Explicit use
    # of the very same root is allowed; a root switch needs a migration protocol.
    if authoritative is not None and not _same(selected, authoritative):
        raise RuntimeError("Native state root conflicts with the existing machine state configuration.")
    if (
        os.getenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", "").strip().casefold() == "true"
        and authoritative is None
    ):
        raise RuntimeError("This Runner still requires machine-scoped state; native mode is not configured for it.")

    program_data = os.getenv("PROGRAMDATA", "").strip()
    legacy_site = (
        _absolute(program_data) / "PersonalAIAssistant" / "autonomous-engineering"
        if program_data else Path(site_data_path("PersonalAIAssistant", appauthor=False)) / "autonomous-engineering"
    )
    legacy_home = Path.home() / ".personal-ai-assistant" / "autonomous-engineering"
    for previous in (legacy_site, legacy_home):
        if not _same(previous, selected) and _existing(previous):
            raise RuntimeError("Existing legacy state requires explicit verified migration; no new state was created.")

    # For a fresh install, require the OS user-data tree, not an arbitrary
    # network or machine-wide location chosen by a request. Existing machine
    # root remains usable only at exactly its authoritative configured path.
    if authoritative is None and not _same(selected, user_root):
        try:
            selected.resolve().relative_to(user_root.resolve())
        except ValueError as exc:
            raise RuntimeError("Fresh native state must be within the OS user-data directory.") from exc
    return selected


def validate_native_state_root(root: Path) -> None:
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RuntimeError("Native state root is not a real directory.")
    if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise PermissionError("Native state root is not owned and protected by the current user.")
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise PermissionError("Native state root is not accessible to the current user.")
