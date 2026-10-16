# Read-only storage health diagnostics

The local endpoint `GET /api/health/storage` checks the three configured SQLite stores:

- settings
- learning and experience/task evidence
- cloud-resource discovery

The endpoint is restricted to loopback clients. It opens existing files in SQLite read-only mode and does not initialize databases, run migrations, repair schema, or create missing files. It reports SQLite integrity, foreign-key violations, migration-history consistency, current schema fingerprint agreement, and whether the store is at the registered migration version.

Possible per-store statuses include:

- `ok`: integrity and foreign keys are clean, migration history matches the registry, the schema fingerprint matches, and the current migration is applied.
- `missing` / `unreadable`: the configured file is absent or could not be inspected.
- `untracked_schema` / `invalid_migration_history`: the store has no trusted migration history or the recorded history is inconsistent.
- `schema_drift`: the live schema differs from the fingerprint expected for its recorded migration version.
- `outdated_schema`: the migration history is valid but is behind the current application schema.
- `integrity_failed` / `foreign_key_violations`: SQLite reports an integrity or referential-integrity problem.

The response deliberately omits absolute paths, raw SQLite errors, table contents, and record values. A degraded result is diagnostic only; it does not attempt automatic repair. Before any recovery or migration, take a verified backup and follow the database recovery procedure.

The existing `GET /api/health` endpoint also no longer returns absolute database paths. It exposes only whether the stores are configured and whether their configured paths are distinct.


The local dashboard includes a **سلامت ذخیره‌سازی** panel that calls this endpoint on page load and offers a manual refresh button. The panel displays the diagnostic response as text; it does not provide repair controls.
