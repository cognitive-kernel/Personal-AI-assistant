# Versioned SQLite migration runner: current scope

## Implemented primitive

`app/schema_migrations.py` provides:

- ordered, application-owned SQL migrations with immutable SHA-256 checksums;
- transactional application with `BEGIN IMMEDIATE`, rollback on any statement failure, and an idempotent no-op when the database is already current;
- per-database `schema_migrations` history containing version, migration ID, checksum, and applied time;
- a logical fingerprint of tables, indexes, views, and triggers; column order and harmless DDL formatting differences are normalized, while column definitions, constraints, foreign keys, and indexes remain part of the fingerprint;
- explicit legacy baseline adoption through `record_baseline()`, which refuses to proceed unless the actual schema fingerprint exactly matches the reviewed expected fingerprint;
- fail-safe errors for untracked existing schemas, missing migration definitions, changed migration IDs/checksums, and version gaps.

## Current implementation status

`app/schema_registry.py` now defines versioned migrations for fresh settings and learning databases, including the learning-job quality field, path-topic relation, task queue, and idempotency index. The registry is tested against fresh in-memory databases and an upgrade fixture from learning schema version 1.

The registry is wired into `db.init_databases()` and `task_manager.initialize_task_store()`. Startup uses the registered settings and learning migrations, seeds missing default settings, and avoids the former scattered `CREATE TABLE`/`ALTER TABLE` schema logic. Before baseline adoption or a forward migration, an existing database receives a verified SQLite backup named `*.pre-migration-<UTC timestamp>.bak`. If its schema is unknown, startup fails without adopting it; the backup remains available for diagnosis. No existing live user database was modified as part of this change.

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

- Settings database: version 1 creates the `settings` table.
- Learning database: version 1 creates learning, assessment, path, and task tables without later-added columns; version 2 adds `learning_jobs.quality_json`; version 3 adds `learning_jobs.path_topic_id`, `task_queue.dedupe_key`, and the related indexes.
- `fingerprint_for_version()` builds a canonical schema fixture in memory from the registered migrations. These fingerprints are candidates for explicit baseline matching, not permission to accept any live schema.


## Safe legacy adoption

`initialize_connection(connection, migrations)` accepts a fresh empty database, a database with valid migration history, or an untracked database whose logical schema fingerprint matches a registered migration version. It records the matched migration's checksum and the exact baseline fingerprint, then applies only later migrations. Unknown schemas, failed integrity checks, and foreign-key violations are rejected without adding history or changing schema.

Column order is normalized because application queries use named columns and historical `ALTER TABLE ADD COLUMN` operations can append a field that later appears inline in a fresh `CREATE TABLE`. This normalization does not ignore column definitions, constraints, foreign keys, or index definitions.


## Startup and backup behavior

- `db.init_databases()` initializes both stores through `initialize_database_file()` and inserts only missing defaults.
- `task_manager.initialize_task_store()` delegates to the same learning migration registry, so standalone callers remain supported and repeated calls are idempotent.
- Existing schema without migration history and existing schema with pending versions are backed up before adoption or upgrade. The backup must pass SQLite `integrity_check` before migration proceeds.
- Already-current databases are not backed up on every restart.
- Unknown schemas are backed up, then rejected without adding migration history or changing their schema/data.


## Schema drift detection

When migration history exists, the runner reconstructs the expected schema for the recorded version and compares it with the live database before applying any later migration. Missing or externally modified tables/indexes are rejected rather than silently repaired or treated as current. Recovery must be an explicit, backed-up migration or restore operation.


## Restore behavior

`restore_database_backup(backup_path, target_path)` validates both SQLite integrity and foreign-key consistency, writes to a temporary file in the target directory, validates the restored copy, and atomically replaces the target only after successful validation. An existing target is never replaced unless the caller explicitly passes `overwrite=True`. Keep a copy of the backup itself until the restored application has been verified.
