"""Local-only, explicit-confirmation API for verified database backups."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool, ConfigDict

from .config import defaults
from .db_backup import BackupRestoreError, backup_configured_databases, restore_databases
from .task_routes import require_loopback_client

logger = logging.getLogger("personal_ai_assistant.storage")
router = APIRouter(
    prefix="/api/storage",
    tags=["storage"],
    dependencies=[Depends(require_loopback_client)],
)
_BACKUP_ID = re.compile(r"^snapshot-\d{8}T\d{12}Z$")


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool


def _backup_root() -> Path:
    return Path(defaults.database_backup_dir).expanduser().resolve()


def list_backups() -> dict[str, Any]:
    """List recognized local backup manifests without exposing filesystem paths."""
    root = _backup_root()
    if not root.exists():
        return {"items": [], "count": 0}
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="The configured backup location is unavailable.")

    items: list[dict[str, Any]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        raise HTTPException(status_code=503, detail="The configured backup location is unavailable.") from None
    for child in children:
        if child.is_symlink() or not child.is_dir() or not _BACKUP_ID.fullmatch(child.name):
            continue
        manifest_path = child / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("format_version") not in (1, 2):
            continue
        databases = manifest.get("databases")
        if not isinstance(databases, dict):
            continue
        roles = sorted(role for role in databases if role in {"settings", "learning", "cloud_resources"})
        items.append({
            "id": child.name,
            "created_at": str(manifest.get("created_at", ""))[:64],
            "format_version": manifest["format_version"],
            "database_roles": roles,
            "complete": set(roles) == {"settings", "learning", "cloud_resources"},
        })
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return {"items": items[:50], "count": min(len(items), 50)}


def create_backup(request: BackupRequest) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required to create a backup.")
    root = _backup_root()
    backup_id = datetime.now(timezone.utc).strftime("snapshot-%Y%m%dT%H%M%S%fZ")
    destination = root / backup_id
    try:
        manifest = backup_configured_databases(destination)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="A backup with this identifier already exists; retry the request.") from None
    except (BackupRestoreError, OSError, ValueError) as exc:
        logger.error("Database backup failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The verified database backup could not be created.") from None
    roles = sorted(manifest.get("databases", {}).keys())
    return {
        "status": "created",
        "id": backup_id,
        "created_at": manifest.get("created_at", ""),
        "format_version": manifest.get("format_version"),
        "database_roles": roles,
        "complete": set(roles) == {"settings", "learning", "cloud_resources"},
        "verified": True,
    }


@router.get("/backups")
def get_backups() -> dict[str, Any]:
    return list_backups()


@router.post("/backups", status_code=201)
def post_backup(request: BackupRequest) -> dict[str, Any]:
    return create_backup(request)


@router.post("/backups/{backup_id}/restore", status_code=201)
def post_restore(backup_id: str, request: BackupRequest) -> dict[str, Any]:
    """Restore a verified backup into a fresh staging directory, never live DB paths."""
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required to stage a restore.")
    if not _BACKUP_ID.fullmatch(backup_id):
        raise HTTPException(status_code=404, detail="Backup not found.")
    root = _backup_root()
    source = root / backup_id
    try:
        if source.is_symlink() or not source.is_dir() or source.resolve().parent != root:
            raise HTTPException(status_code=404, detail="Backup not found.")
        restore_root = root / "restore-staging"
        if restore_root.exists() and (
            restore_root.is_symlink()
            or not restore_root.is_dir()
            or restore_root.resolve().parent != root
        ):
            raise HTTPException(status_code=503, detail="Restore staging location is unavailable.")
        restore_id = datetime.now(timezone.utc).strftime("restore-%Y%m%dT%H%M%S%fZ")
        destination = restore_root / restore_id
        restored = restore_databases(source, destination)
    except HTTPException:
        raise
    except FileExistsError:
        raise HTTPException(status_code=409, detail="A staging restore with this identifier already exists; retry the request.") from None
    except (BackupRestoreError, OSError, ValueError) as exc:
        logger.error("Database restore staging failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=422, detail="The selected backup could not be verified and staged.") from None
    return {
        "status": "staged",
        "source_backup_id": backup_id,
        "restore_id": restore_id,
        "database_roles": sorted(restored),
        "verified": True,
        "live_databases_changed": False,
        "next_step": "Validate the restored application and plan any cutover separately.",
    }
