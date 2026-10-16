"""Explicitly opt-in, read-only client for the official Kaggle quota command.

This client only queries the current account's quota. It never submits a
kernel, creates accounts, changes settings, or stores credentials or output.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .kaggle_quota import KaggleQuotaError, KaggleQuotaSnapshot, parse_quota_csv


class KaggleQuotaClientError(RuntimeError):
    """Raised when an explicitly requested quota check cannot be completed."""


_ALLOWED_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "KAGGLE_API_TOKEN",
    "KAGGLE_USERNAME",
    "KAGGLE_KEY",
    "KAGGLE_CONFIG_DIR",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
)


def _credential_environment() -> dict[str, str]:
    """Pass only CLI path/home and Kaggle credential configuration to the child."""
    return {
        key: os.environ[key]
        for key in _ALLOWED_ENVIRONMENT
        if key in os.environ
    }


def fetch_quota_snapshot(
    *,
    executable_path: str | Path,
    enabled: bool,
    user_consent: bool,
    timeout_seconds: int = 10,
) -> KaggleQuotaSnapshot:
    """Fetch quota only when both feature enablement and explicit user consent are true."""
    if enabled is not True or user_consent is not True:
        raise KaggleQuotaClientError(
            "Kaggle quota access is disabled until the user explicitly enables it and consents."
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be an integer from 1 to 30.")
    executable = Path(executable_path).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise KaggleQuotaClientError("A valid absolute path to the Kaggle CLI executable is required.")

    try:
        result = subprocess.run(
            [str(executable), "quota", "--csv"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=_credential_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise KaggleQuotaClientError("Kaggle quota request timed out.") from exc
    except OSError as exc:
        raise KaggleQuotaClientError("Kaggle CLI could not be started.") from exc

    if result.returncode != 0:
        # Do not propagate stderr: CLI errors may include account or environment details.
        raise KaggleQuotaClientError(
            f"Kaggle quota command failed with exit code {result.returncode}."
        )
    try:
        return parse_quota_csv(
            result.stdout,
            observed_at=datetime.now(timezone.utc),
        )
    except KaggleQuotaError as exc:
        raise KaggleQuotaClientError("Kaggle CLI returned invalid quota data.") from exc
