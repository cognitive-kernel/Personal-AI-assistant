from __future__ import annotations

import os
from pathlib import Path


def execution_state_directory() -> Path:
    """Resolve one stable state root shared by every participating process identity."""
    configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if configured:
        expanded = Path(os.path.expandvars(configured)).expanduser()
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
    if os.name == "nt":
        raise RuntimeError(
            "PROGRAMDATA is unavailable; refusing to fall back to a per-user "
            "autonomous-engineering state directory on Windows."
        )

    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"
