"""Explicitly opted-in, read-only OCI service-limit query using the official CLI.

The client never provisions instances, accepts trial credits as free capacity, or
authorizes remote execution. It reads only the Compute service-limit listing.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .oci_quota import OciQuotaError, OciQuotaSnapshot, parse_limits_json


class OciQuotaClientError(RuntimeError):
    """Raised when an explicitly requested read-only OCI query cannot complete."""


_ALLOWED_ENVIRONMENT = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "TEMP", "TMP")
_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$")
_PROFILE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OCID = re.compile(r"^ocid1\.(?:tenancy|compartment)\.[A-Za-z0-9-]+\.[A-Za-z0-9._-]+$")


def _environment() -> dict[str, str]:
    return {key: os.environ[key] for key in _ALLOWED_ENVIRONMENT if key in os.environ}


def fetch_service_limits(
    *,
    executable_path: str | Path,
    config_file: str | Path,
    profile: str,
    region: str,
    compartment_id: str,
    enabled: bool,
    user_consent: bool,
    timeout_seconds: int = 15,
) -> OciQuotaSnapshot:
    """Query service limits only when feature enablement and explicit consent are true."""
    if enabled is not True or user_consent is not True:
        raise OciQuotaClientError("OCI quota access is disabled until explicitly enabled with user consent.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be an integer from 1 to 30.")
    executable = Path(executable_path).expanduser()
    config = Path(config_file).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise OciQuotaClientError("A valid absolute OCI CLI executable path is required.")
    if not config.is_absolute() or not config.is_file():
        raise OciQuotaClientError("A valid absolute OCI config file path is required.")
    if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
        raise ValueError("profile contains unsupported characters.")
    if not isinstance(region, str) or not _REGION.fullmatch(region):
        raise ValueError("region contains unsupported characters.")
    if not isinstance(compartment_id, str) or not _OCID.fullmatch(compartment_id):
        raise ValueError("compartment_id must be a valid OCI tenancy/compartment OCID.")

    command = [
        str(executable),
        "--config-file", str(config),
        "--profile", profile,
        "--region", region,
        "limits", "value", "list",
        "--compartment-id", compartment_id,
        "--service-name", "compute",
        "--all",
        "--output", "json",
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise OciQuotaClientError("OCI service-limit request timed out.") from exc
    except OSError as exc:
        raise OciQuotaClientError("OCI CLI could not be started.") from exc
    if result.returncode != 0:
        # CLI errors can reveal account configuration; do not propagate stderr.
        raise OciQuotaClientError(
            f"OCI service-limit command failed with exit code {result.returncode}."
        )
    try:
        return parse_limits_json(
            result.stdout,
            observed_at=datetime.now(timezone.utc),
            region=region,
        )
    except OciQuotaError as exc:
        raise OciQuotaClientError("OCI CLI returned invalid service-limit data.") from exc
