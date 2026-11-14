"""Dynamic per-capability concurrency limits for synchronous provider calls."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator


class CapacityWaitTimeout(TimeoutError):
    """Raised when a provider slot cannot be acquired within the configured wait."""


class DynamicCapacityLimiter:
    """A process-local limiter whose capacity can change without restarting workers."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: dict[str, int] = {}

    @contextmanager
    def slot(self, key: str, capacity: int, wait_timeout_seconds: int) -> Iterator[None]:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("capacity key must be non-empty.")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 8:
            raise ValueError("capacity must be an integer between 1 and 8.")
        if (
            isinstance(wait_timeout_seconds, bool)
            or not isinstance(wait_timeout_seconds, int)
            or not 1 <= wait_timeout_seconds <= 3600
        ):
            raise ValueError("wait timeout must be an integer between 1 and 3600 seconds.")

        deadline = time.monotonic() + wait_timeout_seconds
        with self._condition:
            while self._active.get(key, 0) >= capacity:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapacityWaitTimeout(
                        f"Timed out waiting for a provider slot for '{key}'."
                    )
                self._condition.wait(timeout=remaining)
            self._active[key] = self._active.get(key, 0) + 1

        try:
            yield
        finally:
            with self._condition:
                active = self._active.get(key, 0)
                if active <= 1:
                    self._active.pop(key, None)
                else:
                    self._active[key] = active - 1
                self._condition.notify_all()


provider_capacity = DynamicCapacityLimiter()
