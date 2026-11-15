from __future__ import annotations

import os

from app.autonomous_engineering_state import (
    execution_state_directory as _resolve_execution_state_directory,
)


_MACHINE_ENVIRONMENT_KEY = (
    r"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment"
)


def _is_windows() -> bool:
    return os.name == "nt"


def _machine_configured_state_directory() -> str:
    """Read the machine-scoped root using this module's testable platform seam."""
    if not _is_windows():
        return ""
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - Windows standard library
        raise RuntimeError("Windows registry access is required to resolve shared state.") from exc
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENVIRONMENT_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AUTONOMOUS_ENGINEERING_PROGRESS_DIR")
            return str(value).strip()
    except FileNotFoundError:
        return ""


def execution_state_directory():
    """Compatibility facade over the app-packaged shared state contract."""
    return _resolve_execution_state_directory(
        is_windows_fn=_is_windows,
        machine_state_fn=_machine_configured_state_directory,
    )
