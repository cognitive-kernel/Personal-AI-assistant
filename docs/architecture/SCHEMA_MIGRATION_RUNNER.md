# Versioned SQLite migration runner: current scope

## Implemented primitive

`app/schema_migrations.py` provides:

- ordered, application-owned SQL migrations with immutable SHA-256 checksums;
- transactional application with `BEGIN IMMEDIATE`, rollback on any statement failure, and an idempotent no-op when the database is already current;
- per-database `schema_migrations` history containing version, migration ID, checksum, and applied time;
- a canonical fingerprint of tables, indexes, views, and triggers;
- explicit legacy baseline adoption through `record_baseline()`, which refuses to proceed unless the actual schema fingerprint exactly matches the reviewed expected fingerprint;
- fail-safe errors for untracked existing schemas, missing migration definitions, changed migration IDs/checksums, and version gaps.

## Current implementation status

`app/schema_registry.py` now defines versioned migrations for fresh settings and learning databases, including the learning-job quality field, path-topic relation, task queue, and idempotency index. The registry is tested against fresh in-memory databases and an upgrade fixture from learning schema version 1.

The registry is **not yet wired into application startup**. It does not replace the current schema creation in `app/db.py` or `app/task_manager.py`, does not create migration history in a user's live database, and does not migrate or delete any live data.

Do not call `MigrationRunner.apply()` against current application databases until known legacy fingerprints are reviewed and startup adoption is implemented. Existing databases without history are intentionally rejected rather than automatically marked as current.

## API outline

```python
import sqlite3
from app.schema_migrations import Migration, MigrationRunner

migrations = MigrationRunner((
    Migration(1, "create-example", (
        "CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
    )),
    Migration(2, "index-example", (
        "CREATE INDEX idx_example_value ON example(value)",
    )),
))

with sqlite3.connect("new-example.db") as connection:
    migrations.apply(connection)
```

Each SQL statement must be a single statement supported by `Connection.execute()`. Migrations are application-owned code, not user-provided SQL.

## Next step

Build reviewed migration definitions for the settings and learning schemas, with fresh-install migrations and explicit legacy adoption fixtures. Only after the schema signatures, backup/restore, and upgrade tests pass should startup use the runner and the ad-hoc `ALTER TABLE` operations be removed.


## Legacy baseline adoption

A legacy schema must be compared against a fingerprint generated from a reviewed, canonical fixture for the registered migration. Do not calculate the expected fingerprint from the same untrusted live database: that would merely approve its current state.

```python
from app.schema_migrations import Migration, record_baseline, schema_fingerprint

registered_migration = Migration(
    1,
    "create-example",
    ("CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",),
)
# expected_fingerprint must come from the reviewed canonical schema fixture.
record_baseline(
    connection,
    migration=registered_migration,
    expected_fingerprint=expected_fingerprint,
)
```

The history stores the registered migration's checksum and the matched baseline fingerprint separately. The same migration registry can therefore initialize a fresh database or continue forward from an explicitly adopted legacy database.


## Registered application migrations

`app/schema_registry.py` defines:

- Settings database: version 1 creates `settings`.
- Learning database: version 1 creates core learning/provenance/assessment/path tables; version 2 adds `quality_json`; version 3 adds `path_topic_id`; version 4 creates the task queue without `dedupe_key`; version 5 adds `dedupe_key` and its unique partial index.

Reviewed legacy fingerprint variants are mapped to the matching migration version in `SETTINGS_LEGACY_BASELINES` and `LEARNING_LEGACY_BASELINES`. `baseline_known_legacy_schema()` records a baseline only after an exact fingerprint match and refuses unknown schemas without changing them.

These definitions and fixtures are **not yet wired into application startup**. The live DB initialization path still runs the old DDL and conditional `ALTER TABLE` statements. Startup integration is a separate step after CI verifies the legacy fixtures.
