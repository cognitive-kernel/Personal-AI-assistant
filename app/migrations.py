"""Small transactional schema-migration runner for SQLite databases.

Each database owns its own migration history. Existing unversioned databases must
be positively identified by a schema-specific detector before a baseline is recorded.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable

MigrationApply = Callable[[sqlite3.Connection], None]
SchemaDetector = Callable[[sqlite3.Connection], int | None]
SchemaValidator = Callable[[sqlite3.Connection], bool]


class SchemaMigrationError(RuntimeError):
    """Raised when a database schema cannot be safely upgraded or identified."""


@dataclass(frozen=True)
class Migration:
    version: int
    migration_id: str
    checksum: str
    apply: MigrationApply


def sql_migration(version: int, migration_id: str, statement: str) -> Migration:
    """Build a migration whose checksum is derived from its exact SQL statement."""
    checksum = hashlib.sha256(statement.encode("utf-8")).hexdigest()

    def apply(connection: sqlite3.Connection) -> None:
        connection.execute(statement)

    return Migration(version, migration_id, checksum, apply)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _has_migration_table(connection: sqlite3.Connection) -> bool:
    return "schema_migrations" in _table_names(connection)


def _ensure_history_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY CHECK(version > 0),
            migration_id TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            applied_mode TEXT NOT NULL CHECK(applied_mode IN ('migration', 'baseline'))
        )"""
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    }
    expected = {"version", "migration_id", "checksum", "applied_at", "applied_mode"}
    if columns != expected:
        raise SchemaMigrationError(
            "schema_migrations has an unsupported shape; refusing to modify migration history."
        )
    connection.commit()


def _validate_registry(migrations: list[Migration]) -> dict[int, Migration]:
    ordered = sorted(migrations, key=lambda item: item.version)
    versions = [item.version for item in ordered]
    if versions != list(range(1, len(versions) + 1)):
        raise SchemaMigrationError("Migration versions must be unique, contiguous, and start at 1.")
    if any(not item.migration_id.strip() or not item.checksum for item in ordered):
        raise SchemaMigrationError("Every migration requires a migration_id and checksum.")
    if len({item.migration_id for item in ordered}) != len(ordered):
        raise SchemaMigrationError("Migration IDs must be unique.")
    return {item.version: item for item in ordered}


def _history(connection: sqlite3.Connection) -> list[tuple[int, str, str, str]]:
    try:
        rows = connection.execute(
            "SELECT version, migration_id, checksum, applied_mode "
            "FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise SchemaMigrationError("Migration history is unreadable.") from exc
    return [(int(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows]


def run_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration],
    *,
    legacy_version_detector: SchemaDetector | None = None,
    schema_validator: SchemaValidator | None = None,
) -> int:
    """Apply pending migrations or safely adopt a recognized unversioned schema.

    Migrations run one at a time in an IMMEDIATE transaction. Unknown legacy
    schemas and changed checksums fail closed; the runner never guesses a baseline.
    Returns the latest schema version.
    """
    registry = _validate_registry(list(migrations))
    if not registry:
        raise SchemaMigrationError("At least one migration must be registered.")

    existing_tables = _table_names(connection)
    has_history = "schema_migrations" in existing_tables
    domain_tables = existing_tables - {"schema_migrations"}
    history_rows = (
        _history(connection)
        if has_history
        else []
    )

    # A legacy DB is adopted only when the caller can positively identify its
    # exact schema. Do this check before creating metadata so an unknown DB remains
    # untouched when adoption cannot be proved.
    baseline_version: int | None = None
    if not history_rows and domain_tables:
        if legacy_version_detector is None:
            raise SchemaMigrationError(
                "Database has tables but no migration history; no legacy schema detector is configured."
            )
        baseline_version = legacy_version_detector(connection)
        if baseline_version is None or baseline_version not in registry:
            raise SchemaMigrationError(
                "Database schema is unversioned and does not match a recognized legacy baseline."
            )

    if not has_history:
        _ensure_history_table(connection)
    elif not history_rows:
        # Validate the metadata table before attempting to write a baseline or migration.
        _ensure_history_table(connection)

    if baseline_version is not None:
        baseline = registry[baseline_version]
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """INSERT INTO schema_migrations(
                       version, migration_id, checksum, applied_mode
                   ) VALUES (?, ?, ?, 'baseline')""",
                (baseline.version, baseline.migration_id, baseline.checksum),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise SchemaMigrationError(
                f"Could not record recognized legacy baseline version {baseline.version}."
            ) from exc
        history_rows = _history(connection)

    applied = {version: (migration_id, checksum, mode) for version, migration_id, checksum, mode in history_rows}
    if history_rows:
        expected_versions = list(range(1, history_rows[-1][0] + 1))
        if [row[0] for row in history_rows] != expected_versions:
            raise SchemaMigrationError("Migration history has gaps or out-of-order versions.")

    for version, (migration_id, checksum, _mode) in applied.items():
        expected = registry.get(version)
        if expected is None:
            raise SchemaMigrationError(
                f"Database schema version {version} is newer than this application."
            )
        if expected.migration_id != migration_id or expected.checksum != checksum:
            raise SchemaMigrationError(
                f"Migration history mismatch at version {version}; refusing to continue."
            )

    current_version = max(applied, default=0)
    for version in range(current_version + 1, len(registry) + 1):
        migration = registry[version]
        connection.execute("BEGIN IMMEDIATE")
        try:
            migration.apply(connection)
            connection.execute(
                """INSERT INTO schema_migrations(
                       version, migration_id, checksum, applied_mode
                   ) VALUES (?, ?, ?, 'migration')""",
                (migration.version, migration.migration_id, migration.checksum),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise SchemaMigrationError(
                f"Migration {migration.version} ({migration.migration_id}) failed; transaction rolled back."
            ) from exc
        current_version = version

    if schema_validator is not None and not schema_validator(connection):
        raise SchemaMigrationError(
            f"Database failed schema validation after migration version {current_version}."
        )
    return current_version
