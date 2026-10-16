"""Read-only diagnostics for the application's SQLite storage boundaries.

The diagnostic endpoint must never initialize, migrate, repair, or disclose filesystem
paths. It reports enough state to guide recovery without exposing database contents.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .config import defaults
from .schema_migrations import schema_fingerprint
from .schema_registry import (
    CLOUD_RESOURCE_MIGRATIONS,
    LEARNING_MIGRATIONS,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 2000")
    return connection


def _inspect_database(name: str, raw_path: str, migrations: Iterable[Any]) -> dict[str, Any]:
    path = Path(raw_path)
    migration_list = tuple(migrations)
    expected_version = migration_list[-1].version if migration_list else 0
    base: dict[str, Any] = {
        "name": name,
        "status": "unavailable",
        "schema_version": None,
        "expected_schema_version": expected_version,
        "integrity": "not_checked",
        "foreign_key_violations": None,
        "migration_history_valid": False,
        "schema_fingerprint_matches": False,
    }
    if not path.is_file():
        base["status"] = "missing"
        return base

    try:
        with _connect_read_only(path) as connection:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"
            base["integrity"] = "ok" if integrity_ok else "failed"

            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            base["foreign_key_violations"] = len(foreign_key_rows)

            history_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not history_exists:
                base["status"] = "untracked_schema"
                return base

            rows = connection.execute(
                "SELECT version, migration_id, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            known = {item.version: item for item in migration_list}
            history_valid = bool(rows) and len(rows) <= len(migration_list)
            expected_versions = list(range(1, len(rows) + 1))
            history_valid = history_valid and [int(row["version"]) for row in rows] == expected_versions
            if history_valid:
                for row in rows:
                    migration = known.get(int(row["version"]))
                    if migration is None or row["migration_id"] != migration.migration_id or row["checksum"] != migration.checksum:
                        history_valid = False
                        break
            base["migration_history_valid"] = bool(history_valid)
            if rows:
                applied_version = int(rows[-1]["version"])
                base["schema_version"] = applied_version
            else:
                base["status"] = "untracked_schema"
                return base

            if history_valid:
                expected_fingerprint = fingerprint_for_version(migration_list, applied_version)
                base["schema_fingerprint_matches"] = (
                    schema_fingerprint(connection) == expected_fingerprint
                )

            if not integrity_ok:
                base["status"] = "integrity_failed"
            elif base["foreign_key_violations"]:
                base["status"] = "foreign_key_violations"
            elif not history_valid:
                base["status"] = "invalid_migration_history"
            elif not base["schema_fingerprint_matches"]:
                base["status"] = "schema_drift"
            elif applied_version != expected_version:
                base["status"] = "outdated_schema"
            else:
                base["status"] = "ok"
            return base
    except (sqlite3.Error, OSError, ValueError):
        # Keep OS paths, SQLite diagnostics, and database contents out of the API response.
        base["status"] = "unreadable"
        return base


def check_storage_health() -> dict[str, Any]:
    """Inspect configured stores without creating files or changing schema/data."""
    databases = [
        _inspect_database("settings", defaults.settings_db_path, SETTINGS_MIGRATIONS),
        _inspect_database("learning", defaults.learning_db_path, LEARNING_MIGRATIONS),
        _inspect_database(
            "cloud_resources", defaults.cloud_resources_db_path, CLOUD_RESOURCE_MIGRATIONS
        ),
    ]
    all_healthy = all(database["status"] == "ok" for database in databases)
    return {
        "status": "ok" if all_healthy else "degraded",
        "read_only": True,
        "repair_performed": False,
        "databases": databases,
    }
