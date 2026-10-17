"""Explicitly discard the local learning database and recreate its current schema.

This is destructive by design and requires --confirm-reset. It never touches the
settings database or any files outside the configured learning database and its
SQLite WAL/SHM sidecars.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.config import defaults
from app.schema_registry import LEARNING_MIGRATIONS, initialize_database_file


def reset_learning_database(path: Path) -> dict[str, object]:
    """Delete one explicitly selected learning store and initialize a fresh one."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        # Fail safely if the application or another process still holds a write
        # transaction. Do not delete a database that cannot be exclusively locked.
        try:
            with sqlite3.connect(str(path), timeout=2) as connection:
                connection.execute("PRAGMA busy_timeout = 2000")
                connection.execute("BEGIN EXCLUSIVE")
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    connection.rollback()
                    raise RuntimeError(
                        f"Refusing to reset database with failed integrity_check: {integrity}"
                    )
                connection.rollback()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "Could not obtain an exclusive database lock. Stop the app and retry."
            ) from exc

        # Remove only the selected learning database and SQLite's own sidecars.
        for candidate in (
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
            path,
        ):
            if candidate.exists():
                candidate.unlink()

    version = initialize_database_file(str(path), LEARNING_MIGRATIONS)

    with sqlite3.connect(str(path)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        history = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    if integrity != "ok" or version != LEARNING_MIGRATIONS[-1].version:
        raise RuntimeError(
            f"Fresh database verification failed: version={version}, integrity={integrity}"
        )

    return {
        "database": str(path),
        "reset_performed": True,
        "schema_version": version,
        "migration_versions": [row[0] for row in history],
        "table_count": len(tables),
        "integrity_check": integrity,
        "settings_database_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "DESTRUCTIVE: discard the configured learning database and recreate "
            "it using the current registered migrations."
        )
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="Required confirmation; without it no database is changed.",
    )
    args = parser.parse_args()
    if not args.confirm_reset:
        parser.error("This operation deletes learning data. Pass --confirm-reset explicitly.")

    result = reset_learning_database(Path(defaults.learning_db_path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
