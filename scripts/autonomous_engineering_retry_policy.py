"""Pure retry-budget contract for one autonomous engineering run."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bound retries by both count and elapsed time without discarding task state."""

    max_attempts: int
    max_elapsed_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")
        if (
            isinstance(self.max_elapsed_seconds, bool)
            or not isinstance(self.max_elapsed_seconds, (int, float))
            or not math.isfinite(float(self.max_elapsed_seconds))
            or self.max_elapsed_seconds <= 0
        ):
            raise ValueError("max_elapsed_seconds must be a positive finite number.")

    def allows_attempt(self, attempts_completed: int, elapsed_seconds: float) -> bool:
        if isinstance(attempts_completed, bool) or not isinstance(attempts_completed, int) or attempts_completed < 0:
            raise ValueError("attempts_completed must be a non-negative integer.")
        if (
            isinstance(elapsed_seconds, bool)
            or not isinstance(elapsed_seconds, (int, float))
            or not math.isfinite(float(elapsed_seconds))
            or elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be a non-negative finite number.")
        return attempts_completed < self.max_attempts and elapsed_seconds < self.max_elapsed_seconds

    def remaining_seconds(self, elapsed_seconds: float) -> int:
        if (
            isinstance(elapsed_seconds, bool)
            or not isinstance(elapsed_seconds, (int, float))
            or not math.isfinite(float(elapsed_seconds))
            or elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be a non-negative finite number.")
        return max(0, int(self.max_elapsed_seconds - elapsed_seconds))

    def exhaustion_reason(self, attempts_completed: int, elapsed_seconds: float) -> str:
        if attempts_completed >= self.max_attempts:
            return f"per-run attempt limit reached ({self.max_attempts})."
        if elapsed_seconds >= self.max_elapsed_seconds:
            return f"per-run task time budget reached ({int(self.max_elapsed_seconds)} seconds)."
        return ""
