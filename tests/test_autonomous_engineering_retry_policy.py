from __future__ import annotations

import pytest

from scripts.autonomous_engineering_retry_policy import RetryPolicy


def test_retry_policy_allows_attempts_only_within_both_budgets():
    policy = RetryPolicy(max_attempts=2, max_elapsed_seconds=7200)

    assert policy.allows_attempt(0, 0)
    assert policy.allows_attempt(1, 7199.99)
    assert not policy.allows_attempt(2, 1)
    assert not policy.allows_attempt(1, 7200)


def test_retry_policy_reports_the_exhausted_budget_without_ambiguity():
    policy = RetryPolicy(max_attempts=2, max_elapsed_seconds=7200)

    assert policy.exhaustion_reason(2, 1) == "per-run attempt limit reached (2)."
    assert policy.exhaustion_reason(1, 7200) == "per-run task time budget reached (7200 seconds)."
    assert policy.exhaustion_reason(1, 100) == ""


@pytest.mark.parametrize(
    ("attempts", "elapsed"),
    [(True, 0), (-1, 0), (0, True), (0, float("nan")), (0, float("inf"))],
)
def test_retry_policy_rejects_invalid_runtime_observations(attempts, elapsed):
    policy = RetryPolicy(max_attempts=2, max_elapsed_seconds=7200)

    with pytest.raises(ValueError):
        policy.allows_attempt(attempts, elapsed)


def test_retry_policy_reports_remaining_seconds_without_rounding_up():
    policy = RetryPolicy(max_attempts=2, max_elapsed_seconds=7200)

    assert policy.remaining_seconds(0) == 7200
    assert policy.remaining_seconds(7199.5) == 0
    assert policy.remaining_seconds(7200) == 0


def test_retry_policy_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0, max_elapsed_seconds=7200)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=3, max_elapsed_seconds=float("inf"))
