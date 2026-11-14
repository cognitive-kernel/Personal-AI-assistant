import threading

import pytest

from app.concurrency import CapacityWaitTimeout, DynamicCapacityLimiter


def test_capacity_one_serializes_calls_without_losing_waiters():
    limiter = DynamicCapacityLimiter()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with limiter.slot("chat", 1, 5):
            first_entered.set()
            assert release_first.wait(timeout=3)

    def second():
        with limiter.slot("chat", 1, 5):
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    assert first_entered.wait(timeout=2)
    second_thread.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    assert second_entered.wait(timeout=2)
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()


def test_capacity_can_be_increased_without_restarting_limiter():
    limiter = DynamicCapacityLimiter()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with limiter.slot("learning", 1, 5):
            first_entered.set()
            assert release_first.wait(timeout=3)

    def second():
        with limiter.slot("learning", 2, 5):
            second_entered.set()

    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert first_entered.wait(timeout=2)
    second_thread = threading.Thread(target=second)
    second_thread.start()
    assert second_entered.wait(timeout=2)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()


def test_capacity_wait_timeout_is_explicit_and_releases_no_slot():
    limiter = DynamicCapacityLimiter()
    with limiter.slot("embedding", 1, 1):
        with pytest.raises(CapacityWaitTimeout, match="embedding"):
            with limiter.slot("embedding", 1, 1):
                pytest.fail("second call must not enter while the first slot is held")


@pytest.mark.parametrize(
    ("capacity", "timeout"),
    [(0, 1), (9, 1), (True, 1), (1, 0), (1, 3601), (1, True)],
)
def test_capacity_contract_rejects_invalid_values(capacity, timeout):
    limiter = DynamicCapacityLimiter()
    with pytest.raises(ValueError):
        with limiter.slot("chat", capacity, timeout):
            pytest.fail("invalid capacity must be rejected before entering")
