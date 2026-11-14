"""Public facade for the modular sequential-learning coordinator."""

from .learning_topic_method_attempts import METHOD_ORDER, MethodAttemptError
from .learning_method_queue import queue_initial_method, queue_method_attempt
from .learning_method_transitions import (
    advance_after_failure,
    block_unavailable,
    block_without_quality_gate,
    complete_method,
    mark_method_cancelled,
    mark_method_running,
    record_generation_failure,
)

__all__ = [
    "METHOD_ORDER",
    "MethodAttemptError",
    "advance_after_failure",
    "block_unavailable",
    "block_without_quality_gate",
    "complete_method",
    "mark_method_cancelled",
    "mark_method_running",
    "queue_initial_method",
    "queue_method_attempt",
    "record_generation_failure",
]
