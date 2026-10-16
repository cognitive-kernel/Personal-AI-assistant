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
