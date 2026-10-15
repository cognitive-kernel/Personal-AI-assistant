"""Transactional, versioned SQLite schema migration primitives.

Existing schemas are never adopted automatically: explicit baseline adoption
requires an exact match with a reviewed schema fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable

_HISTORY_TABLE = "schema_migrations"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MigrationError(RuntimeError):
    """Base error for migration history or application failures."""


class UntrackedSchemaError(MigrationError):
    """Raised when a database has schema but no trusted migration history."""


@dataclass(frozen=True)
class Migration:
    version: int
    migration_id: str
    statements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Migration version must be a positive integer.")
        if not isinstance(self.migration_id, str) or not self.migration_id.strip():
            raise ValueError("Migration ID must be a non-empty string.")
        if not isinstance(self.statements, tuple) or any(
            not isinstance(statement, str) or not statement.strip()
            for statement in self.statements
        ):
            raise ValueError("Migration statements must be a tuple of non-empty SQL strings.")

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            {
                "version": self.version,
                "migration_id": self.migration_id,
                "statements": [statement.strip() for statement in self.statements],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash canonical SQLite DDL, excluding internal tables and migration metadata."""
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' AND name != ?
          AND type IN ('table', 'index', 'view', 'trigger')
        ORDER BY type, name, tbl_name, sql
        """,
        (_HISTORY_TABLE,),
    ).fetchall()
    canonical = json.dumps([tuple(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _has_user_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%' AND name != ?
        LIMIT 1
        """,
        (_HISTORY_TABLE,),
    ).fetchone()
    return row is not None


def _has_history_table(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_HISTORY_TABLE,),
    ).fetchone() is not None


def _create_history_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK(version > 0),
            migration_id TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL CHECK(length(checksum) = 64),
            baseline_fingerprint TEXT,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def record_baseline(
    connection: sqlite3.Connection,
    *,
    migration: Migration,
    expected_fingerprint: str,
) -> Migration:
    """Adopt an existing schema as a migration only after exact fingerprint match."""
    if connection.in_transaction:
        raise ValueError("record_baseline requires a connection with no active transaction.")
    if not isinstance(migration, Migration):
        raise TypeError("migration must be a Migration instance.")
    if _has_history_table(connection):
        raise MigrationError("Migration history already exists; baseline adoption is not allowed.")
    if not _HEX_SHA256.fullmatch(expected_fingerprint):
        raise ValueError("Expected fingerprint must be a lowercase SHA-256 hex digest.")
    if schema_fingerprint(connection) != expected_fingerprint:
        raise UntrackedSchemaError(
            "Legacy schema fingerprint does not match the reviewed baseline; refusing adoption."
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        _create_history_table(connection)
        connection.execute(
            """INSERT INTO schema_migrations(
                   version, migration_id, checksum, baseline_fingerprint
               ) VALUES (?, ?, ?, ?)""",
            (migration.version, migration.migration_id, migration.checksum, expected_fingerprint),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return migration


class MigrationRunner:
    """Apply application-owned SQL migrations in order and verify immutable history."""

    def __init__(self, migrations: Iterable[Migration]) -> None:
        self.migrations = tuple(migrations)
        versions = [migration.version for migration in self.migrations]
        ids = [migration.migration_id for migration in self.migrations]
        if versions != sorted(set(versions)):
            raise ValueError("Migration versions must be unique and strictly increasing.")
        if len(ids) != len(set(ids)):
            raise ValueError("Migration IDs must be unique.")

    def apply(self, connection: sqlite3.Connection) -> int:
        """Apply pending migrations. Return the latest applied version, or zero."""
        if connection.in_transaction:
            raise ValueError("MigrationRunner.apply requires a connection with no active transaction.")
        if not self.migrations:
            raise ValueError("At least one migration must be registered.")

        history_exists = _has_history_table(connection)
        user_schema_exists = _has_user_schema(connection)
        if not history_exists and user_schema_exists:
            raise UntrackedSchemaError(
                "Database has schema but no migration history; fingerprint and explicitly baseline it first."
            )
        if history_exists:
            history_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
            }
            if not {"version", "migration_id", "checksum", "baseline_fingerprint"} <= history_columns:
                raise MigrationError("Migration history schema is incompatible; manual review is required.")
            rows = connection.execute(
                "SELECT version, migration_id, checksum, baseline_fingerprint "
                "FROM schema_migrations ORDER BY version"
            ).fetchall()
            if not rows and user_schema_exists:
                raise UntrackedSchemaError(
                    "Database has schema but an empty migration history; explicit baseline is required."
                )
        else:
            rows = []

        registry = {migration.version: migration for migration in self.migrations}
        current_version = 0
        if rows:
            versions = [int(row[0]) for row in rows]
            if versions != sorted(set(versions)):
                raise MigrationError("Migration history contains duplicate or unordered versions.")
            first = registry.get(versions[0])
            first_baseline = rows[0][3]
            if versions[0] != 1 and first_baseline is None:
                raise MigrationError("Migration history has a gap before its first version.")
            if first_baseline is not None and not _HEX_SHA256.fullmatch(first_baseline):
                raise MigrationError("Migration baseline fingerprint is invalid.")
            if any(row[3] is not None for row in rows[1:]):
                raise MigrationError("Only the first recorded migration may be a baseline.")
            for previous, version in zip(versions, versions[1:]):
                if version != previous + 1:
                    raise MigrationError("Migration history contains a version gap.")
            for row in rows:
                version = int(row[0])
                migration = registry.get(version)
                if migration is None:
                    raise MigrationError(f"Applied migration version {version} is not registered.")
                if migration.migration_id != row[1]:
                    raise MigrationError(f"Migration ID changed for version {version}.")
                if migration.checksum != row[2]:
                    raise MigrationError(f"Migration checksum changed for version {version}.")
            current_version = versions[-1]
            if first_baseline is not None and current_version == versions[0]:
                if schema_fingerprint(connection) != first_baseline:
                    raise MigrationError("Legacy schema changed after baseline adoption; refusing migration.")

        pending = [migration for migration in self.migrations if migration.version > current_version]
        if not pending:
            return current_version
        expected_next = current_version + 1
        for migration in pending:
            if migration.version != expected_next:
                raise MigrationError(f"Expected migration version {expected_next}, found {migration.version}.")
            expected_next += 1

        try:
            connection.execute("BEGIN IMMEDIATE")
            if not history_exists:
                _create_history_table(connection)
            for migration in pending:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    """INSERT INTO schema_migrations(
                           version, migration_id, checksum, baseline_fingerprint
                       ) VALUES (?, ?, ?, NULL)""",
                    (migration.version, migration.migration_id, migration.checksum),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return pending[-1].version
