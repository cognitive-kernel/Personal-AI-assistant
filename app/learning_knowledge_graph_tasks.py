"""Queue handler for bounded evidence review of learning knowledge graph edges."""
from __future__ import annotations

from typing import Any

from . import learning_knowledge_graph
from .task_worker import TaskContext

KNOWLEDGE_GRAPH_REVIEW_TASK_TYPE = "learning.review_graph_edge"
_ALLOWED_KEYS = {"edge_id", "source_sha256"}


def handle_review_graph_edge(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_KEYS:
        raise ValueError("Knowledge graph review payload has an unsupported schema.")
    edge_id = payload.get("edge_id")
    source_hash = payload.get("source_sha256")
    if isinstance(edge_id, bool) or not isinstance(edge_id, int) or edge_id < 1:
        raise ValueError("edge_id must be a positive integer.")
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise ValueError("source_sha256 must be a SHA-256 digest.")
    context.checkpoint({"stage": "graph_edge_review_started", "edge_id": edge_id})
    result = learning_knowledge_graph.review_edge(edge_id, expected_source_sha256=source_hash, context=context)
    context.checkpoint({"stage": "graph_edge_review_finished", "edge_id": edge_id, "status": result["status"]})
    return result
