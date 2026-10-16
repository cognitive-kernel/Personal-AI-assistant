# Verified database backup and restore

The application now supports a complete backup bundle for the three configured SQLite stores: settings, learning/experience/task data, and cloud-resource discovery data.

## Create a complete bundle

Call `app.db_backup.backup_configured_databases(destination_dir)` after the application has initialized its stores. The destination must not already exist. The function:

1. snapshots each database through SQLite's online backup API;
2. runs SQLite integrity and foreign-key checks on each snapshot;
3. records each snapshot's SHA-256, schema version, and table inventory;
4. writes a manifest using format version 2; and
5. publishes the bundle with an atomic directory rename only after every snapshot passes validation.

The backup directory contains `settings.sqlite3`, `learning.sqlite3`, `cloud_resources.sqlite3`, and `manifest.json`. Store contents and absolute source paths are not copied into the manifest.

## Restore

Call `restore_databases(backup_dir, destination_dir)` to restore into a new directory. The destination must not already exist. The restore path verifies the manifest format, exact expected filenames, SHA-256 values, table inventory, SQLite integrity, and foreign-key constraints before publishing the restored directory. It never overwrites an existing destination.

Format-version-1 bundles created by the older two-store API remain restorable for compatibility. They contain only settings and learning data; they do not contain cloud-resource discovery state. Format-version-2 bundles contain all three stores. For new backups, use `backup_configured_databases` or pass `cloud_resources_path` to `backup_databases`.

## Operational limits

- This module creates and verifies a bundle; it does not automatically replace live databases.
- A successful integrity check does not prove that every business-level invariant is correct.
- Restore into a staging directory first, validate the restored application, and only then plan any controlled cutover.
- Backups contain private local data and should be protected by filesystem permissions and an appropriate retention policy.


## Local dashboard and API

The local dashboard exposes an explicit **ایجاد پشتیبان کامل** action. The user must confirm in the browser before the request is sent. The loopback-only endpoints are:

- `GET /api/storage/backups` — list up to 50 recognized backup manifests without exposing filesystem paths.
- `POST /api/storage/backups` with JSON `{"confirm": true}` — create a complete format-v2 bundle in the configured `DATABASE_BACKUP_DIR` (default: `data/backups`).

The API generates the backup identifier itself; clients cannot choose an arbitrary filesystem path. The endpoint returns the bundle identifier, database roles, and verification result, not the absolute path. The UI does not offer automatic restore or overwrite operations.


## Dashboard/API staged restore

The loopback-only endpoint `POST /api/storage/backups/{backup_id}/restore` accepts only `{"confirm": true}`. The backup identifier must match the server-generated snapshot ID format and resolve to a real backup directly inside the configured backup root. The restore process verifies manifest, hashes, schema inventory, SQLite integrity and foreign keys, then creates a new directory under `restore-staging/`. The response contains a generated staging ID and database roles, never absolute paths.

This endpoint **does not modify the live databases**, overwrite a destination, or perform a cutover. Inspect and validate the staged files and application behavior separately before planning any manual deployment. Failed validation does not publish a staging directory. The staging directory contains private application data and should be protected and cleaned up under an explicit operational policy.


## Retention and cleanup

The local dashboard offers preview-first cleanup through `POST /api/storage/backups/prune`. The preview request uses `{"confirm": false, "keep_last": 3, "min_age_days": 30}` and does not delete anything. After displaying the eligible server-generated snapshot IDs, the UI asks for a second explicit confirmation before sending `confirm: true`.

The policy preserves at least the three newest recognized snapshots and never considers snapshots younger than 30 days for deletion. The API permits a keep count of 3–50 and an age threshold of 30–3650 days. Only direct child directories with recognized snapshot IDs and a compatible manifest/file inventory are eligible; symbolic links, staging restores, and unrecognized folders are skipped. This is a manually initiated cleanup, not an automatic scheduler. Keep external/off-device copies for disaster recovery.


## Optional periodic backups

Periodic backups are disabled by default. Set `DATABASE_BACKUP_INTERVAL_HOURS` to an integer from 1 to 168 to enable a background scheduler; leave it at `0` to disable it. The first snapshot is created only after one full interval, not during application startup. Each run uses the SQLite online backup API and verifies all three configured stores before publishing the bundle. Failures are logged and do not stop chat or learning. The scheduler is stopped during application shutdown.

The scheduler does not restore, overwrite live databases, or delete old backups. Because scheduled snapshots accumulate, enable it only when disk capacity is sufficient and use the manual preview-first retention control periodically. Automatic deletion and off-device replication are deliberately not enabled.

## Restore-staging retention

The dashboard also provides preview-first cleanup through `POST /api/storage/restore-staging/prune`. The default policy keeps the newest staging restore and only considers verified staging snapshots at least seven days old. The preview does not delete anything; deletion requires a separate explicit confirmation. The API rechecks the selected directory and SQLite integrity immediately before removal.

Only generated restore IDs directly inside `restore-staging/` with the expected database files and valid SQLite integrity checks are eligible. Symlinks, malformed folders, unknown files, recent restores, and the newest restore are preserved. This cleanup does not touch original backup bundles or live databases. It is manual and local-only; staging data can contain sensitive application information.
