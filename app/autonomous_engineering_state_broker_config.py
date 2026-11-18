"""Validated machine-wide POSIX broker configuration shared by all local accounts."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

_CONFIG_NAME = "autonomous-state-broker.json"
_MAX_CONFIG_BYTES = 8192


def config_directory() -> Path:
    try:
        from platformdirs import site_config_path
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("platformdirs is required to locate machine broker configuration.") from exc
    directory = Path(site_config_path("PersonalAIAssistant", appauthor=False))
    if not directory.is_absolute():
        raise RuntimeError("Machine broker configuration directory must be absolute.")
    return directory


def config_file() -> Path:
    return config_directory() / _CONFIG_NAME


def load_config(*, missing_ok: bool = True) -> dict[str, Any] | None:
    path = config_file()
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("Machine broker configuration must be a regular file.")
    if info.st_size > _MAX_CONFIG_BYTES:
        raise RuntimeError("Machine broker configuration exceeds its size limit.")
    # Other local accounts may read the endpoint but must not be able to replace it.
    if info.st_mode & 0o022:
        raise PermissionError("Machine broker configuration is writable by a group or other local users.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError("Machine broker configuration is unreadable or malformed.") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("Machine broker configuration schema is unsupported.")
    state_dir = value.get("state_directory")
    socket_path = value.get("socket_path")
    service_uid = value.get("service_uid")
    if (
        not isinstance(state_dir, str)
        or not Path(state_dir).is_absolute()
        or not isinstance(socket_path, str)
        or not Path(socket_path).is_absolute()
        or isinstance(service_uid, bool)
        or not isinstance(service_uid, int)
        or service_uid < 0
    ):
        raise RuntimeError("Machine broker configuration contains invalid paths or service identity.")
    if info.st_uid not in {0, service_uid}:
        raise PermissionError("Machine broker configuration owner does not match the broker service identity.")
    process_override = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if process_override:
        import os.path
        expanded = os.path.abspath(os.path.expandvars(process_override))
        configured = os.path.abspath(os.path.expandvars(state_dir))
        if os.path.normcase(expanded) != os.path.normcase(configured):
            raise RuntimeError(
                "Process AUTONOMOUS_ENGINEERING_PROGRESS_DIR differs from the machine broker state root."
            )
    return value


def configured_state_directory() -> Path | None:
    value = load_config()
    return Path(value["state_directory"]) if value else None


def configured_socket_path() -> Path:
    value = load_config(missing_ok=False)
    assert value is not None
    return Path(value["socket_path"])
