"""Local-only, explicit-confirmation API for verified database backups."""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool, ConfigDict, Field

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


class BackupPruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool
    keep_last: int = Field(default=3, ge=3, le=50)
    min_age_days: int = Field(default=30, ge=30, le=3650)


class RestoreStagingPruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool
    keep_last: int = Field(default=1, ge=1, le=20)
    min_age_days: int = Field(default=7, ge=7, le=3650)


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
        if restore_root.is_symlink() or (
            restore_root.exists()
            and (not restore_root.is_dir() or restore_root.resolve().parent != root)
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

def _retention_candidates(keep_last: int, min_age_days: int) -> dict[str, Any]:
    root = _backup_root()
    if not root.exists():
        return {"eligible": [], "preserved": 0}
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="The configured backup location is unavailable.")
    recognized: list[tuple[float, str, Path]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        raise HTTPException(status_code=503, detail="The configured backup location is unavailable.") from None
    for child in children:
        if child.is_symlink() or not child.is_dir() or not _BACKUP_ID.fullmatch(child.name):
            continue
        if child.resolve().parent != root:
            continue
        manifest_path = child / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                continue
            version = manifest.get("format_version")
            expected_roles = {"settings", "learning"} if version == 1 else (
                {"settings", "learning", "cloud_resources"} if version == 2 else set()
            )
            databases = manifest.get("databases")
            if not expected_roles or not isinstance(databases, dict) or set(databases) != expected_roles:
                continue
            expected_files = {
                "settings": "settings.sqlite3",
                "learning": "learning.sqlite3",
                "cloud_resources": "cloud_resources.sqlite3",
            }
            valid = True
            for role in expected_roles:
                metadata = databases.get(role)
                filename = expected_files[role]
                file_path = child / filename
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("filename") != filename
                    or file_path.is_symlink()
                    or not file_path.is_file()
                ):
                    valid = False
                    break
            if not valid:
                continue
            modified = child.stat(follow_symlinks=False).st_mtime
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        recognized.append((modified, child.name, child))

    recognized.sort(key=lambda item: (item[0], item[1]), reverse=True)
    protected = {name for _, name, _ in recognized[:keep_last]}
    cutoff = time.time() - min_age_days * 86400
    eligible = [
        {"id": name, "modified_at": datetime.fromtimestamp(modified, timezone.utc).isoformat().replace("+00:00", "Z")}
        for modified, name, _ in recognized
        if name not in protected and modified <= cutoff
    ]
    return {"eligible": eligible, "preserved": len(recognized) - len(eligible)}


def prune_backups(request: BackupPruneRequest) -> dict[str, Any]:
    plan = _retention_candidates(request.keep_last, request.min_age_days)
    eligible = plan["eligible"]
    if request.confirm is not True:
        return {
            "status": "preview",
            "confirm_required": bool(eligible),
            "keep_last": request.keep_last,
            "min_age_days": request.min_age_days,
            "would_delete_count": len(eligible),
            "candidates": eligible,
            "preserved_count": plan["preserved"],
            "deleted": [],
        }

    root = _backup_root()
    deleted: list[str] = []
    failed: list[str] = []
    for item in eligible:
        backup_id = item["id"]
        if not _BACKUP_ID.fullmatch(backup_id):
            continue
        path = root / backup_id
        try:
            if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
                failed.append(backup_id)
                continue
            shutil.rmtree(path)
            deleted.append(backup_id)
        except OSError:
            logger.warning("Could not prune backup %s.", backup_id)
            failed.append(backup_id)
    return {
        "status": "completed" if not failed else "partial",
        "keep_last": request.keep_last,
        "min_age_days": request.min_age_days,
        "deleted": deleted,
        "failed": failed,
        "deleted_count": len(deleted),
        "preserved_count": plan["preserved"],
    }


@router.post("/backups/prune")
def post_prune_backups(request: BackupPruneRequest) -> dict[str, Any]:
    return prune_backups(request)


def _valid_staging_snapshot(path: Path) -> bool:
    """Recognize only complete, non-symlink SQLite restore snapshots."""
    expected = {
        "settings.sqlite3",
        "learning.sqlite3",
        "cloud_resources.sqlite3",
    }
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        names = {item.name for item in path.iterdir()}
        if names not in (
            {"settings.sqlite3", "learning.sqlite3"},
            expected,
        ):
            return False
        for name in names:
            database_path = path / name
            if database_path.is_symlink() or not database_path.is_file():
                return False
            uri = database_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    return False
        return True
    except (OSError, sqlite3.Error):
        return False


def _staging_retention_candidates(keep_last: int, min_age_days: int) -> dict[str, Any]:
    root = _backup_root() / "restore-staging"
    if not root.exists():
        return {"eligible": [], "preserved": 0}
    if root.is_symlink() or not root.is_dir() or root.resolve().parent != _backup_root():
        raise HTTPException(status_code=503, detail="Restore staging location is unavailable.")
    recognized: list[tuple[float, str, Path]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        raise HTTPException(status_code=503, detail="Restore staging location is unavailable.") from None
    for child in children:
        if child.is_symlink() or not child.is_dir() or not _RESTORE_ID.fullmatch(child.name):
            continue
        if child.resolve().parent != root or not _valid_staging_snapshot(child):
            continue
        try:
            modified = child.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        recognized.append((modified, child.name, child))
    recognized.sort(key=lambda item: (item[0], item[1]), reverse=True)
    protected = {name for _, name, _ in recognized[:keep_last]}
    cutoff = time.time() - min_age_days * 86400
    eligible = [
        {
            "id": name,
            "modified_at": datetime.fromtimestamp(modified, timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        for modified, name, _ in recognized
        if name not in protected and modified <= cutoff
    ]
    return {"eligible": eligible, "preserved": len(recognized) - len(eligible)}


def prune_restore_staging(request: RestoreStagingPruneRequest) -> dict[str, Any]:
    plan = _staging_retention_candidates(request.keep_last, request.min_age_days)
    eligible = plan["eligible"]
    if request.confirm is not True:
        return {
            "status": "preview",
            "confirm_required": bool(eligible),
            "keep_last": request.keep_last,
            "min_age_days": request.min_age_days,
            "would_delete_count": len(eligible),
            "candidates": eligible,
            "preserved_count": plan["preserved"],
            "deleted": [],
        }

    root = _backup_root() / "restore-staging"
    deleted: list[str] = []
    failed: list[str] = []
    for item in eligible:
        restore_id = item["id"]
        path = root / restore_id
        try:
            if (
                not _RESTORE_ID.fullmatch(restore_id)
                or path.is_symlink()
                or not path.is_dir()
                or path.resolve().parent != root
                or not _valid_staging_snapshot(path)
            ):
                failed.append(restore_id)
                continue
            shutil.rmtree(path)
            deleted.append(restore_id)
        except OSError:
            logger.warning("Could not prune restore staging snapshot %s.", restore_id)
            failed.append(restore_id)
    return {
        "status": "completed" if not failed else "partial",
        "keep_last": request.keep_last,
        "min_age_days": request.min_age_days,
        "deleted": deleted,
        "failed": failed,
        "deleted_count": len(deleted),
        "preserved_count": plan["preserved"],
    }


@router.post("/restore-staging/prune")
def post_prune_restore_staging(request: RestoreStagingPruneRequest) -> dict[str, Any]:
    return prune_restore_staging(request)
