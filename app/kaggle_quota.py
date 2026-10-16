"""Strict parser for user-authorized Kaggle CLI quota snapshots.

This module never launches the Kaggle CLI or reads credentials. Callers must
obtain output through an explicitly authorized provider adapter and keep the
snapshot short-lived because account quotas can change outside this process.
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class KaggleQuotaError(ValueError):
    """Raised when quota output is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class ResourceQuota:
    used_hours: float
    remaining_hours: float
    total_hours: float


@dataclass(frozen=True)
class KaggleQuotaSnapshot:
    observed_at: datetime
    refresh_at: datetime
    gpu: ResourceQuota | None
    tpu: ResourceQuota | None

    def is_fresh(
        self,
        *,
        now: datetime,
        max_age: timedelta = timedelta(minutes=15),
    ) -> bool:
        """A snapshot is usable only before reset and within a bounded age."""
        _require_aware_datetime(now, "now")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive.")
        age = now - self.observed_at
        return timedelta(0) <= age <= max_age and now < self.refresh_at

    def available_hours(self, resource: str) -> float | None:
        selected = resource.strip().casefold()
        if selected == "gpu":
            quota = self.gpu
        elif selected == "tpu":
            quota = self.tpu
        else:
            raise ValueError("resource must be GPU or TPU.")
        return None if quota is None else quota.remaining_hours


def _require_aware_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def _parse_hours(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*h\s*", value, re.IGNORECASE)
    if match is None:
        raise KaggleQuotaError("Quota durations must use the documented hours format, e.g. 12.50h.")
    number = float(match.group(1))
    if not math.isfinite(number) or number < 0:
        raise KaggleQuotaError("Quota hours must be finite and non-negative.")
    return number


def _parse_refresh_at(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise KaggleQuotaError("Quota output is missing refreshAt.")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise KaggleQuotaError("refreshAt must be an ISO-8601 timestamp.") from exc
    # Kaggle's CLI treats timezone-naive quota refresh timestamps as UTC.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_quota_csv(
    output: str,
    *,
    observed_at: datetime,
) -> KaggleQuotaSnapshot:
    """Parse kaggle quota CSV output without executing a command or storing secrets."""
    _require_aware_datetime(observed_at, "observed_at")
    if not isinstance(output, str) or not output.strip() or len(output) > 32_000:
        raise KaggleQuotaError("Quota output must be non-empty text of at most 32 KB.")

    reader = csv.DictReader(io.StringIO(output))
    if not reader.fieldnames:
        raise KaggleQuotaError("Quota CSV has no header.")
    headers = {field.strip().casefold(): field for field in reader.fieldnames if field}
    required = {"resource", "used", "remaining", "total", "refreshat"}
    if not required <= set(headers):
        raise KaggleQuotaError("Quota CSV must contain resource, used, remaining, total, and refreshAt columns.")

    rows: dict[str, ResourceQuota] = {}
    refresh_values: set[datetime] = set()
    for row in reader:
        resource_name = (row.get(headers["resource"]) or "").strip().casefold()
        if resource_name not in {"gpu", "tpu"}:
            raise KaggleQuotaError("Quota CSV contains an unknown or empty resource name.")
        if resource_name in rows:
            raise KaggleQuotaError(f"Quota CSV contains duplicate {resource_name.upper()} rows.")
        used = _parse_hours(row.get(headers["used"]) or "")
        remaining = _parse_hours(row.get(headers["remaining"]) or "")
        total = _parse_hours(row.get(headers["total"]) or "")
        if used > total + 0.03 or remaining > total + 0.03:
            raise KaggleQuotaError("Quota used/remaining hours exceed the reported total.")
        if abs((used + remaining) - total) > 0.05:
            raise KaggleQuotaError("Quota used + remaining does not match total within display rounding.")
        refresh_values.add(_parse_refresh_at(row.get(headers["refreshat"]) or ""))
        rows[resource_name] = ResourceQuota(
            used_hours=used,
            remaining_hours=remaining,
            total_hours=total,
        )

    if not rows:
        raise KaggleQuotaError("Quota CSV contains no GPU or TPU quota rows.")
    if len(refresh_values) != 1:
        raise KaggleQuotaError("GPU and TPU rows must report the same quota refresh time.")
    refresh_at = next(iter(refresh_values))
    observed = observed_at.astimezone(timezone.utc)
    if refresh_at <= observed:
        raise KaggleQuotaError("Quota refresh time is not in the future; refresh the snapshot.")
    return KaggleQuotaSnapshot(
        observed_at=observed,
        refresh_at=refresh_at,
        gpu=rows.get("gpu"),
        tpu=rows.get("tpu"),
    )
