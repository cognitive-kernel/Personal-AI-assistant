from __future__ import annotations

import os
from pathlib import Path


_MACHINE_ENVIRONMENT_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)


def _is_windows() -> bool:
    return os.name == "nt"


def _machine_configured_state_directory() -> str:
    """Read the machine-scoped state root; never trust a per-user override on Windows."""
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


def execution_state_directory() -> Path:
    """Resolve one stable state root shared by every participating process identity."""
    process_configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if _is_windows():
        machine_configured = _machine_configured_state_directory()
        require_machine_scope = (
            os.getenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", "").strip().lower()
            == "true"
        )
        if process_configured and not machine_configured and require_machine_scope:
            raise RuntimeError(
                "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process/user-scoped; configure it "
                "at machine scope so all Windows accounts share one state root."
            )
        if machine_configured:
            machine_path = os.path.expandvars(machine_configured).strip()
            if not Path(machine_path).is_absolute():
                raise ValueError(
                    "Machine AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute path."
                )
            if process_configured:
                process_path = os.path.expandvars(process_configured).strip()
                if not Path(process_path).is_absolute() or (
                    os.path.normcase(os.path.abspath(process_path))
                    != os.path.normcase(os.path.abspath(machine_path))
                ):
                    raise RuntimeError(
                        "Process AUTONOMOUS_ENGINEERING_PROGRESS_DIR differs from the "
                        "machine-scoped state root; refusing per-account state divergence."
                    )
            return Path(machine_path)

    if process_configured:
        expanded = Path(os.path.expandvars(process_configured)).expanduser()
        if not expanded.is_absolute():
            raise ValueError(
                "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute path so "
                "different working directories and Windows accounts resolve the same state."
            )
        return expanded

    program_data = os.getenv("PROGRAMDATA", "").strip()
    if program_data:
        shared_root = Path(os.path.expandvars(program_data)).expanduser()
        if not shared_root.is_absolute():
            raise RuntimeError("PROGRAMDATA must resolve to an absolute shared path.")
        return shared_root / "PersonalAIAssistant" / "autonomous-engineering"
    if _is_windows():
        raise RuntimeError(
            "PROGRAMDATA is unavailable; refusing to fall back to a per-user "
            "autonomous-engineering state directory on Windows."
        )

    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"
