"""Public facade for method lifecycle transitions.

Generation failures and evaluator outcomes are implemented in independent modules;
callers keep a stable import path while each transition family owns its own logic.
"""

from .learning_method_generation_transitions import (
    advance_after_failure,
    block_without_quality_gate,
    mark_method_cancelled,
    mark_method_running,
    record_generation_failure,
)
from .learning_method_evaluation_transitions import (
    block_unavailable,
    complete_method,
)

__all__ = [
    "advance_after_failure",
    "block_unavailable",
    "block_without_quality_gate",
    "complete_method",
    "mark_method_cancelled",
    "mark_method_running",
    "record_generation_failure",
]
