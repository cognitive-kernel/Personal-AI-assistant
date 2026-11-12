from __future__ import annotations

import os
from pathlib import Path


def execution_state_directory() -> Path:
    """Resolve the shared local state root used by controller and checkpoint processes."""
    configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "PersonalAIAssistant" / "autonomous-engineering"
    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"
