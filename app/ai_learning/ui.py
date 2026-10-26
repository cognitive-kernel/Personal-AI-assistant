"""Optional management page and static assets for AI knowledge capture."""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from .config import is_enabled
from ..task_routes import require_loopback_client

router = APIRouter(tags=["ai-learning-ui"], dependencies=[Depends(require_loopback_client)])
_ROOT = Path(__file__).resolve().parent / "static"
_ASSETS = {"app.js": "text/javascript", "style.css": "text/css"}

@router.get("/ai-learning", include_in_schema=False)
def learning_dashboard() -> FileResponse:
    if not is_enabled():
        raise HTTPException(status_code=404, detail="AI-learning module is disabled.")
    return FileResponse(_ROOT / "index.html", media_type="text/html")

@router.get("/ai-learning-assets/{asset_name}", include_in_schema=False)
def learning_asset(asset_name: str) -> FileResponse:
    if not is_enabled():
        raise HTTPException(status_code=404, detail="AI-learning module is disabled.")
    media_type = _ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(_ROOT / asset_name, media_type=media_type)
