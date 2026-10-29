"""Verified, local-only backup and restore for the application's SQLite databases.

Backups are created as a new directory containing verified snapshots and a
manifest. New complete bundles include settings, learning, and cloud-resource
stores. Restores are staged and published only after every included file passes
hash and SQLite integrity checks. Existing destinations are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
LEGACY_FORMAT_VERSION = 1
FORMAT_VERSION = 2
LEGACY_DATABASE_FILES = {"settings": "settings.sqlite3", "learning": "learning.sqlite3"}
DATABASE_FILES = {
    **LEGACY_DATABASE_FILES,
    "cloud_resources": "cloud_resources.sqlite3",
}


class BackupRestoreError(RuntimeError):
    """Raised when a database backup cannot be verified or safely restored."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_database(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise BackupRestoreError(f"SQLite database does not exist or is empty: {path.name}")
    try:
        with closing(sqlite3.connect(str(path), timeout=10)) as connection:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise BackupRestoreError(f"SQLite integrity check failed for {path.name}: {integrity[:5]}")
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise BackupRestoreError(
                    f"Foreign-key check failed for {path.name}: {len(foreign_key_errors)} violation(s)"
                )
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
    except sqlite3.DatabaseError as exc:
        raise BackupRestoreError(f"Invalid SQLite database {path.name}: {exc}") from exc
    return {"user_version": user_version, "tables": tables}


def _snapshot(source_path: Path, destination_path: Path) -> dict[str, Any]:
    if not source_path.is_file():
        raise BackupRestoreError(f"Source database is missing: {source_path}")
    try:
        with closing(sqlite3.connect(str(source_path), timeout=10)) as source:
            with closing(sqlite3.connect(str(destination_path), timeout=10)) as destination:
                source.backup(destination, pages=256, sleep=0.05)
    except sqlite3.DatabaseError as exc:
        raise BackupRestoreError(f"Could not snapshot {source_path.name}: {exc}") from exc
    metadata = _inspect_database(destination_path)
    metadata["sha256"] = _sha256(destination_path)
    return metadata


def backup_databases(
    settings_path: str | os.PathLike[str],
    learning_path: str | os.PathLike[str],
    destination_dir: str | os.PathLike[str],
    *,
    cloud_resources_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Create a verified backup bundle.

    Supplying cloud_resources_path creates a complete format-v2 bundle with all
    three application stores. Omitting it preserves the legacy two-store
    format-v1 writer for existing callers; restore supports both formats.
    Publication uses an atomic directory rename after all snapshots are verified.
    """
    sources = {
        "settings": Path(settings_path).expanduser().resolve(),
        "learning": Path(learning_path).expanduser().resolve(),
    }
    if cloud_resources_path is not None:
        sources["cloud_resources"] = Path(cloud_resources_path).expanduser().resolve()
    destination = Path(destination_dir).expanduser().resolve()
    if len(set(sources.values())) != len(sources):
        raise ValueError("All database paths must be distinct files.")
    if destination.exists():
        raise FileExistsError(f"Backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        databases: dict[str, Any] = {}
        files = DATABASE_FILES if cloud_resources_path is not None else LEGACY_DATABASE_FILES
        for role, source in sources.items():
            filename = files[role]
            metadata = _snapshot(source, temp_dir / filename)
            metadata["filename"] = filename
            databases[role] = metadata
        manifest = {
            "format_version": FORMAT_VERSION if cloud_resources_path is not None else LEGACY_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "databases": databases,
        }
        manifest_path = temp_dir / MANIFEST_NAME
        with manifest_path.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_dir, destination)
        return manifest
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def backup_configured_databases(destination_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Back up all currently configured application stores as one verified bundle."""
    from .config import defaults

    return backup_databases(
        defaults.settings_db_path,
        defaults.learning_db_path,
        destination_dir,
        cloud_resources_path=defaults.cloud_resources_db_path,
    )

def restore_databases(
    backup_dir: str | os.PathLike[str],
    destination_dir: str | os.PathLike[str],
) -> dict[str, str]:
    """Restore a verified bundle into a new directory without overwriting data."""
    source = Path(backup_dir).expanduser().resolve()
    destination = Path(destination_dir).expanduser().resolve()
    if source == destination or source in destination.parents:
        raise ValueError("Restore destination must be outside the backup directory.")
    if destination.exists():
        raise FileExistsError(f"Restore destination already exists: {destination}")
    manifest_path = source / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise BackupRestoreError("Backup manifest must not be a symbolic link.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("Backup manifest is missing or invalid.") from exc
    if not isinstance(manifest, dict):
        raise BackupRestoreError("Unsupported backup manifest format.")
    format_version = manifest.get("format_version")
    if format_version == LEGACY_FORMAT_VERSION:
        database_files = LEGACY_DATABASE_FILES
    elif format_version == FORMAT_VERSION:
        database_files = DATABASE_FILES
    else:
        raise BackupRestoreError("Unsupported backup manifest format.")
    database_manifest = manifest.get("databases")
    if not isinstance(database_manifest, dict) or set(database_manifest) != set(database_files):
        raise BackupRestoreError("Backup manifest database roles do not match its format version.")

    for role, filename in database_files.items():
        metadata = database_manifest.get(role)
        if not isinstance(metadata, dict) or metadata.get("filename") != filename:
            raise BackupRestoreError(f"Invalid manifest entry for {role}.")
        backup_file = source / filename
        if backup_file.is_symlink():
            raise BackupRestoreError(f"Backup database for {role} must not be a symbolic link.")
        if not backup_file.is_file() or _sha256(backup_file) != metadata.get("sha256"):
            raise BackupRestoreError(f"Backup checksum verification failed for {role}.")
        actual = _inspect_database(backup_file)
        if actual["user_version"] != metadata.get("user_version") or actual["tables"] != metadata.get("tables"):
            raise BackupRestoreError(f"Backup schema metadata verification failed for {role}.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    try:
        for role, filename in database_files.items():
            _snapshot(source / filename, temp_dir / filename)
        os.replace(temp_dir, destination)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {role: str(destination / filename) for role, filename in database_files.items()}
