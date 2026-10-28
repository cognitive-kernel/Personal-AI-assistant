"""Loopback-only API for managing the allowlisted local source index."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import knowledge_base
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/knowledge/sources",
    tags=["knowledge-source-index"],
    dependencies=[Depends(require_loopback_client)],
)


class SourceIngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(extra="forbid")


class SourceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)

    model_config = ConfigDict(extra="forbid")


@router.post("")
def ingest_knowledge_source(request: SourceIngestRequest) -> dict[str, Any]:
    try:
        return knowledge_base.ingest_url(request.url, title=request.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="The approved source could not be fetched.") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="The local source index is unavailable.") from exc


@router.get("")
def list_knowledge_sources() -> dict[str, Any]:
    try:
        return {"sources": knowledge_base.list_sources()}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="The local source index is unavailable.") from exc


@router.post("/search")
def search_knowledge_sources(request: SourceSearchRequest) -> dict[str, Any]:
    try:
        return {
            "query": request.query,
            "results": knowledge_base.search(request.query, limit=request.limit),
        }
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="The source search request is invalid.") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="The local source index is unavailable.") from exc
