"""Loopback API for proposing, reviewing, and retrieving accepted knowledge graph edges."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import db, learning_knowledge_graph, task_manager
from .learning_knowledge_graph_tasks import KNOWLEDGE_GRAPH_REVIEW_TASK_TYPE
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/learning/knowledge-graph",
    tags=["learning-knowledge-graph"],
    dependencies=[Depends(require_loopback_client)],
)


class EdgeProposalRequest(BaseModel):
    source_knowledge_id: int = Field(ge=1)
    target_knowledge_id: int = Field(ge=1)
    relation_type: Literal["supports", "causes", "depends_on", "contradicts", "supersedes"]
    source_url: str = Field(min_length=9, max_length=1500)
    source_excerpt: str = Field(min_length=20, max_length=1000)

    model_config = ConfigDict(extra="forbid")


@router.post("/edges", status_code=201)
def create_graph_edge_proposal(request: EdgeProposalRequest) -> dict[str, Any]:
    try:
        edge = learning_knowledge_graph.propose_edge(
            request.source_knowledge_id,
            request.target_knowledge_id,
            relation_type=request.relation_type,
            source_url=request.source_url,
            source_excerpt=request.source_excerpt,
        )
        task = None
        if edge["status"] in {"proposed", "conflicting"}:
            try:
                task = task_manager.create_task(
                    KNOWLEDGE_GRAPH_REVIEW_TASK_TYPE,
                    {"edge_id": int(edge["id"]), "source_sha256": str(edge["source_sha256"])},
                    max_attempts=1,
                    dedupe_key=f"knowledge-graph-review:{edge['id']}:{edge['source_sha256']}",
                )
            except Exception:
                # Keep the proposal staged; it remains excluded from graph retrieval until reviewed.
                task = None
        return {**edge, "review_task": task}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Retained knowledge item not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/edges/{edge_id}/review", status_code=202)
def enqueue_graph_edge_review(edge_id: int) -> dict[str, Any]:
    if isinstance(edge_id, bool) or edge_id < 1:
        raise HTTPException(status_code=422, detail="edge_id must be positive.")
    with db.learning_connection() as connection:
        edge = connection.execute(
            "SELECT id, source_sha256, status FROM learning_knowledge_graph_edges WHERE id=?",
            (edge_id,),
        ).fetchone()
    if edge is None:
        raise HTTPException(status_code=404, detail="Knowledge graph edge not found.")
    if edge["status"] in {"accepted", "rejected"}:
        return {"edge_id": edge_id, "status": edge["status"], "task": None}
    payload = {"edge_id": edge_id, "source_sha256": edge["source_sha256"]}
    task = task_manager.create_task(
        KNOWLEDGE_GRAPH_REVIEW_TASK_TYPE,
        payload,
        max_attempts=1,
    )
    return {"edge_id": edge_id, "status": "queued", "task": task}


@router.get("/context")
def read_graph_context(topic: str = Query(min_length=1, max_length=300)) -> dict[str, Any]:
    try:
        return learning_knowledge_graph.get_graph_context_for_topic(topic)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
