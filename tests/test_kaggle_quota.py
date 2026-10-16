from datetime import datetime, timedelta, timezone

import pytest

from app.kaggle_quota import KaggleQuotaError, parse_quota_csv


OBSERVED = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)
VALID_CSV = """resource,used,remaining,total,refreshAt
GPU,2.50h,7.50h,10.00h,2026-10-17T00:00:00+00:00
TPU,1.00h,19.00h,20.00h,2026-10-17T00:00:00+00:00
"""


def test_parse_quota_csv_and_expose_account_specific_remaining_hours():
    snapshot = parse_quota_csv(VALID_CSV, observed_at=OBSERVED)

    assert snapshot.gpu.used_hours == 2.5
    assert snapshot.gpu.remaining_hours == 7.5
    assert snapshot.gpu.total_hours == 10.0
    assert snapshot.tpu.remaining_hours == 19.0
    assert snapshot.available_hours("GPU") == 7.5
    assert snapshot.available_hours("tpu") == 19.0
    assert snapshot.refresh_at == datetime(2026, 10, 17, tzinfo=timezone.utc)


def test_quota_snapshot_freshness_is_bounded_and_expires_at_refresh():
    snapshot = parse_quota_csv(VALID_CSV, observed_at=OBSERVED)

    assert snapshot.is_fresh(now=OBSERVED + timedelta(minutes=10))
    assert not snapshot.is_fresh(now=OBSERVED - timedelta(seconds=1))
    assert not snapshot.is_fresh(now=OBSERVED + timedelta(minutes=16))
    assert not snapshot.is_fresh(now=snapshot.refresh_at)
    with pytest.raises(ValueError, match="max_age"):
        snapshot.is_fresh(now=OBSERVED, max_age=timedelta(0))


@pytest.mark.parametrize(
    "output",
    [
        "",
        "resource,used\nGPU,1h",
        "resource,used,remaining,total,refreshAt\nGPU,NaNh,2h,2h,2026-10-17T00:00:00Z",
        "resource,used,remaining,total,refreshAt\nGPU,-1h,3h,2h,2026-10-17T00:00:00Z",
        "resource,used,remaining,total,refreshAt\nGPU,2h,2h,3h,2026-10-17T00:00:00Z",
        "resource,used,remaining,total,refreshAt\nGPU,1h,1h,2h,not-a-date",
        "resource,used,remaining,total,refreshAt\nGPU,1h,1h,2h,2026-10-15T00:00:00Z",
        "resource,used,remaining,total,refreshAt\nGPU,1h,1h,2h,2026-10-17T00:00:00Z\nGPU,1h,1h,2h,2026-10-17T00:00:00Z",
        "resource,used,remaining,total,refreshAt\nASIC,1h,1h,2h,2026-10-17T00:00:00Z",
    ],
)
def test_reject_malformed_or_inconsistent_quota_output(output):
    with pytest.raises(KaggleQuotaError):
        parse_quota_csv(output, observed_at=OBSERVED)


def test_reject_naive_observation_time_and_unknown_resource_lookup():
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_quota_csv(VALID_CSV, observed_at=datetime(2026, 10, 16, 10, 0))

    snapshot = parse_quota_csv(VALID_CSV, observed_at=OBSERVED)
    with pytest.raises(ValueError, match="GPU or TPU"):
        snapshot.available_hours("CPU")


def test_missing_one_accelerator_is_not_inferred_as_zero_quota():
    output = """resource,used,remaining,total,refreshAt
GPU,0.00h,10.00h,10.00h,2026-10-17T00:00:00Z
"""
    snapshot = parse_quota_csv(output, observed_at=OBSERVED)

    assert snapshot.gpu.remaining_hours == 10
    assert snapshot.tpu is None
    assert snapshot.available_hours("TPU") is None
