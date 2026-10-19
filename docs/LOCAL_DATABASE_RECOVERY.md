# Recovering from an untracked local learning schema

The application refuses to start when an existing SQLite database does not match a reviewed migration fingerprint. This prevents it from silently recording an incorrect migration history or changing an unknown schema.

## Safe first response

1. Stop Uvicorn and any other process using the database.
2. Do **not** delete, reset, manually stamp, or migrate the existing database.
3. Run the read-only schema inspector from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\inspect_sqlite_schema.py .\data\learning.db --kind learning --output C:\Temp\learning-schema-report.json
   ```

4. Verify that the report says `"read_only": true`. The inspector reports schema DDL and metadata, not table row contents. Keep the report local because schema details can still be sensitive.
5. Preserve a separate, verified SQLite backup before any future migration attempt. A file copy alone may be insufficient if SQLite WAL files are active; stop the application and use SQLite's backup API or the project's verified backup workflow.
6. Review the fingerprint and every schema object against a reproduced fixture. Unknown or mixed schemas must remain fail-closed.

## Recovery status

A matching registered migration or a reviewed, exact-match legacy migration is required before startup can safely continue. The inspector is diagnostic only; it does not repair the database. Do not use a reset as a workaround for a database that contains valuable learning, source, feedback, or knowledge records.

After an approved migration has been tested against a disposable copy and data preservation verified, start the application:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then verify `http://127.0.0.1:8000/api/health`, the dashboard, curriculum loading, settings, and retained learning/knowledge records.
