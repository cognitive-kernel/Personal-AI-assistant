"""Deterministic per-job request/token budget for lesson generation and repair.

The budget is intentionally conservative and local to one generation pipeline.
It bounds requested output tokens and model calls; it does not estimate monetary
cost or enforce process-wide concurrency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LearningPipelineBudget:
    max_calls: int = 2
    max_predict_tokens: int = 2600
    calls_used: int = 0
    predict_tokens_reserved: int = 0
    reservations: list[dict[str, Any]] = field(default_factory=list)
    _stages: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if isinstance(self.max_calls, bool) or not isinstance(self.max_calls, int) or self.max_calls < 1:
            raise ValueError("max_calls must be a positive integer.")
        if isinstance(self.max_predict_tokens, bool) or not isinstance(self.max_predict_tokens, int) or self.max_predict_tokens < 1:
            raise ValueError("max_predict_tokens must be a positive integer.")

    def reserve(self, stage: str, *, predict_tokens: int) -> bool:
        """Reserve a stage once; return False instead of exceeding either ceiling."""
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 80:
            raise ValueError("stage must be a non-empty string of at most 80 characters.")
        if isinstance(predict_tokens, bool) or not isinstance(predict_tokens, int) or predict_tokens < 1:
            raise ValueError("predict_tokens must be a positive integer.")
        stage = stage.strip()
        if stage in self._stages:
            return False
        if self.calls_used + 1 > self.max_calls:
            return False
        if self.predict_tokens_reserved + predict_tokens > self.max_predict_tokens:
            return False
        self._stages.add(stage)
        self.calls_used += 1
        self.predict_tokens_reserved += predict_tokens
        self.reservations.append({"stage": stage, "predict_tokens": predict_tokens})
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "calls_used": self.calls_used,
            "max_predict_tokens": self.max_predict_tokens,
            "predict_tokens_reserved": self.predict_tokens_reserved,
            "budget_exhausted": (
                self.calls_used >= self.max_calls
                or self.predict_tokens_reserved >= self.max_predict_tokens
            ),
            "reservations": [dict(item) for item in self.reservations],
            "billing_cost_known": False,
            "scope": "single_lesson_generation_job",
        }
