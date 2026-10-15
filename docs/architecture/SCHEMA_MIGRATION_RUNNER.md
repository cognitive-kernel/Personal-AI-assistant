# Versioned SQLite migration runner: current scope

## Implemented primitive

`app/schema_migrations.py` provides:

- ordered, application-owned SQL migrations with immutable SHA-256 checksums;
- transactional application with `BEGIN IMMEDIATE`, rollback on any statement failure, and an idempotent no-op when the database is already current;
- per-database `schema_migrations` history containing version, migration ID, checksum, and applied time;
- a canonical fingerprint of tables, indexes, views, and triggers;
- explicit legacy baseline adoption through `record_baseline()`, which refuses to proceed unless the actual schema fingerprint exactly matches the reviewed expected fingerprint;
- fail-safe errors for untracked existing schemas, missing migration definitions, changed migration IDs/checksums, and version gaps.

## Fresh-install registries defined, startup still unchanged

`app/schema_registry.py` now defines ordered fresh-install migrations for the settings database and for the learning database plus task queue. Tests apply these registries to empty in-memory SQLite databases and validate the resulting tables, columns, foreign keys, and integrity.

The runner is still **not wired into application startup**. It does not create migration history in a user's live database, and it does not migrate or delete any data.

Do not call `MigrationRunner.apply()` against the current application databases until a migration registry and a verified baseline for the actual schema are defined. Existing databases without history are intentionally rejected rather than automatically marked as current.

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

Create canonical legacy fixtures and reviewed fingerprints for every supported existing schema shape. The expected fingerprint must be generated from the fixture, never from the live untracked database. Add tests for baseline adoption and forward migration from each supported legacy version. Only after these tests and backup/restore checks pass should startup call the runner and the ad-hoc startup `ALTER TABLE` operations be removed.


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
