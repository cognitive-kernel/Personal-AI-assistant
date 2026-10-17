# Recovering from an untracked local learning schema

The application intentionally refuses to start when an existing SQLite database
does not match a reviewed migration fingerprint. This prevents accidental data
loss or silently recording an incorrect migration history.

If the local learning database contains only disposable test data, stop the
application and run the explicit reset utility from the repository root:

```powershell
.\.venv\Scripts\python.exe .\scripts\reset_learning_database.py --confirm-reset
```

**Destructive operation:** this permanently deletes the configured learning
database and its SQLite `-wal` / `-shm` sidecar files before creating a fresh
database at the latest registered learning schema version. It does not touch
`settings.db`, source code, benchmark reports, or other files. The script
requires the explicit `--confirm-reset` flag and refuses to proceed if it
cannot obtain an exclusive database lock or the existing database fails its
integrity check. Stop Uvicorn and any other process using the database first.

After a successful reset, start the application again:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then verify `http://127.0.0.1:8000/api/health` and the dashboard. This utility
is for an explicitly disposable local database; do not use it for a production
or otherwise valuable learning store. It is not an automatic startup repair.
