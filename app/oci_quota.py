"""Strict parser for read-only Oracle Cloud Infrastructure service-limit snapshots.

These values describe account/region service limits, not proof that a resource is
Always Free, currently available, or safe to provision.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


class OciQuotaError(ValueError):
    """Raised when an OCI limits response is malformed or ambiguous."""


@dataclass(frozen=True)
class OciServiceLimit:
    name: str
    value: float
    used: float | None
    available: float | None
    scope_type: str | None
    scope_value: str | None


@dataclass(frozen=True)
class OciQuotaSnapshot:
    observed_at: datetime
    region: str
    limits: tuple[OciServiceLimit, ...]

    def is_fresh(
        self,
        *,
        now: datetime,
        max_age: timedelta = timedelta(minutes=5),
    ) -> bool:
        _require_aware(now, "now")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive.")
        age = now.astimezone(timezone.utc) - self.observed_at
        return timedelta(0) <= age <= max_age


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def _number(value: Any, field: str, *, required: bool) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OciQuotaError(f"OCI limit field {field} must be a finite non-negative number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise OciQuotaError(f"OCI limit field {field} must be a finite non-negative number.")
    return parsed


def parse_limits_json(
    output: str,
    *,
    observed_at: datetime,
    region: str,
) -> OciQuotaSnapshot:
    """Parse OCI CLI JSON without invoking the CLI or retaining credentials/raw output."""
    _require_aware(observed_at, "observed_at")
    if not isinstance(region, str) or not region.strip() or len(region) > 80:
        raise ValueError("region must be non-empty text of at most 80 characters.")
    if not isinstance(output, str) or not output.strip() or len(output) > 64_000:
        raise OciQuotaError("OCI limits output must be non-empty text of at most 64 KB.")
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OciQuotaError("OCI limits output is not valid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise OciQuotaError("OCI limits output must contain a data array.")
    if not payload["data"] or len(payload["data"]) > 500:
        raise OciQuotaError("OCI limits data must contain between 1 and 500 records.")

    limits: list[OciServiceLimit] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for row in payload["data"]:
        if not isinstance(row, dict):
            raise OciQuotaError("OCI limits data contains a non-object record.")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise OciQuotaError("OCI limit name is missing or invalid.")
        scope_type = row.get("scope-type")
        scope_value = row.get("scope-value")
        if scope_type is not None and (not isinstance(scope_type, str) or len(scope_type) > 80):
            raise OciQuotaError("OCI limit scope-type is invalid.")
        if scope_value is not None and (not isinstance(scope_value, str) or len(scope_value) > 200):
            raise OciQuotaError("OCI limit scope-value is invalid.")
        key = (name.strip(), scope_type, scope_value)
        if key in seen:
            raise OciQuotaError("OCI limits output contains duplicate limit/scope records.")
        seen.add(key)

        value = _number(row.get("value"), "value", required=True)
        used = _number(row.get("used"), "used", required=False)
        available = _number(row.get("available"), "available", required=False)
        if used is not None and used > value:
            raise OciQuotaError("OCI limit used value exceeds the configured limit.")
        if available is not None and available > value:
            raise OciQuotaError("OCI limit available value exceeds the configured limit.")
        if used is not None and available is not None and used + available > value + 1e-6:
            raise OciQuotaError("OCI limit used + available exceeds the configured limit.")
        limits.append(
            OciServiceLimit(
                name=name.strip(),
                value=value,
                used=used,
                available=available,
                scope_type=scope_type,
                scope_value=scope_value,
            )
        )

    return OciQuotaSnapshot(
        observed_at=observed_at.astimezone(timezone.utc),
        region=region.strip(),
        limits=tuple(limits),
    )
