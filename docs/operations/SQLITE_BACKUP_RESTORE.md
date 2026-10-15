# SQLite backup and restore

This utility backs up the application's two local SQLite databases as a verified bundle. It does not upload data or use cloud storage.

## Create a backup

Stop is not required for the SQLite online backup API to create a consistent snapshot of each individual database. For the strongest cross-database operational consistency, stop the application before starting the backup.

Run from the project root:

```python
from app.config import defaults
from app.db_backup import backup_databases

manifest = backup_databases(
    defaults.settings_db_path,
    defaults.learning_db_path,
    "/absolute/local/path/personal-ai-backup-001",
)
print(manifest)
```

The destination directory must not already exist. It contains:
- `settings.sqlite3` — settings database snapshot.
- `learning.sqlite3` — learning, knowledge, assessment, path, and task-queue snapshot.
- `manifest.json` — format version, creation time, SHA-256 hashes, SQLite user versions, and table names.

The bundle is published only after both snapshots pass `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. Keep the bundle on local storage under your control; it may contain private learning source text, assessment records, and settings.

## Restore into a separate directory

Stop the application first. Restore deliberately creates a new directory and never overwrites an existing destination:

```python
from app.db_backup import restore_databases

paths = restore_databases(
    "/absolute/local/path/personal-ai-backup-001",
    "/absolute/local/path/personal-ai-restored-001",
)
print(paths)
```

The restore validates the manifest, checks SHA-256 hashes, validates schema metadata, copies both databases into a staging directory, runs SQLite integrity checks, and publishes the directory only after validation succeeds. The returned paths point to `settings.sqlite3` and `learning.sqlite3` in the restored directory.

To use the restored copy, configure `SETTINGS_DB_PATH` and `LEARNING_DB_PATH` to those returned paths before restarting the application. Verify `/api/health` and the expected learning/task records before treating the restored copy as production data.

## Safety and limitations

- Existing backup and restore destination directories are rejected; the utility does not silently overwrite them.
- A modified or incomplete backup bundle is rejected before restore.
- The two databases are snapshotted independently; there is no cross-file transaction. Stop the app for the strongest consistency between settings and learning state.
- This is a local backup/restore utility, not a scheduled backup service, off-site disaster-recovery system, encryption tool, or retention manager.
- A backup is not considered proven until restore tests pass. Do not delete the original databases based only on successful backup creation.
