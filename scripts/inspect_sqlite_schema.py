"""Read-only SQLite schema diagnostics for untracked legacy databases.

This tool never opens a database for writing, applies migrations, repairs data,
or prints table row contents. It reports schema DDL so an unknown legacy schema
can be reviewed before any adoption or migration is proposed.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from app.schema_migrations import schema_fingerprint
from app.schema_registry import (
    LEARNING_MIGRATIONS,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
)


REGISTRIES = {
    "learning": LEARNING_MIGRATIONS,
    "settings": SETTINGS_MIGRATIONS,
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inspect_database(path: Path, kind: str) -> dict:
    """Inspect one existing database through SQLite's read-only URI mode."""
    if kind not in REGISTRIES:
        raise ValueError(f"Unsupported database kind: {kind}")
    if not path.is_file():
        return {
            "database": path.name,
            "kind": kind,
            "exists": False,
        }

    # Use an absolute URI and URL-encode special characters (including spaces).
    uri = "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        fingerprint = schema_fingerprint(connection)
        migrations = REGISTRIES[kind]
        matching_versions = [
            migration.version
            for migration in migrations
            if fingerprint == fingerprint_for_version(migrations, migration.version)
        ]
        objects = connection.execute(
            """SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
               FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()

        schema_objects = []
        for obj in objects:
            item = {
                "type": obj["type"],
                "name": obj["name"],
                "table": obj["tbl_name"],
                "sql": obj["sql"],
            }
            if obj["type"] == "table":
                identifier = _quote_identifier(obj["name"])
                item["columns"] = [
                    {
                        "name": column["name"],
                        "type": column["type"],
                        "not_null": bool(column["notnull"]),
                        "default": column["dflt_value"],
                        "primary_key_position": column["pk"],
                    }
                    for column in connection.execute(
                        f"PRAGMA table_info({identifier})"
                    ).fetchall()
                ]
                item["foreign_keys"] = [
                    {
                        "table": foreign_key["table"],
                        "from": foreign_key["from"],
                        "to": foreign_key["to"],
                        "on_update": foreign_key["on_update"],
                        "on_delete": foreign_key["on_delete"],
                    }
                    for foreign_key in connection.execute(
                        f"PRAGMA foreign_key_list({identifier})"
                    ).fetchall()
                ]
            schema_objects.append(item)

        try:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError as exc:
            integrity = [f"error: {type(exc).__name__}"]
        try:
            foreign_key_violations = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        except sqlite3.DatabaseError:
            foreign_key_violations = None

        history = [
            dict(row)
            for row in connection.execute(
                """SELECT version, migration_id, checksum, baseline_fingerprint, applied_at
                   FROM schema_migrations ORDER BY version"""
            ).fetchall()
        ] if any(obj["name"] == "schema_migrations" and obj["type"] == "table"
                 for obj in objects) else []

        return {
            "database": path.name,
            "kind": kind,
            "exists": True,
            "read_only": True,
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "integrity_check": integrity,
            "foreign_key_violation_count": foreign_key_violations,
            "schema_fingerprint": fingerprint,
            "matching_registered_versions": matching_versions,
            "latest_registered_version": migrations[-1].version,
            "migration_history": history,
            "objects": schema_objects,
            "safety_note": (
                "Schema and metadata only; no row contents were read or emitted. "
                "No migrations or repairs were performed."
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only SQLite schema report; never runs migrations."
    )
    parser.add_argument("database", type=Path, help="Path to an existing SQLite database")
    parser.add_argument(
        "--kind", choices=sorted(REGISTRIES), required=True,
        help="Which registered schema to compare against",
    )
    parser.add_argument(
        "--output", type=Path,
        help="Optional output JSON path. By default, print JSON to stdout.",
    )
    args = parser.parse_args()
    report = inspect_database(args.database, args.kind)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "exists": report.get("exists", False),
            "matching_registered_versions": report.get("matching_registered_versions", []),
            "read_only": report.get("read_only", True),
        }, ensure_ascii=False))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
