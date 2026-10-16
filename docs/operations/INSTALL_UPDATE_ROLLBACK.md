# Local installation, update, and rollback runbook

This procedure is for a single-machine, local-first deployment. It does not authorize LAN exposure, remote execution, cloud provisioning, or automatic production database replacement.

## Before an update

1. Schedule a maintenance window and stop the application cleanly.
2. Record the exact currently installed commit:
   ```bash
   git status --short
   git rev-parse HEAD
   ```
   Do not continue with uncommitted source changes until they have been preserved and reviewed.
3. Create a **complete three-store backup** using the application's backup UI/API. Follow [Database backup and restore](../DATABASE_BACKUP.md). Keep the bundle outside the repository and protect it as sensitive local data.
4. Restore that backup to a **new staging destination** and verify the manifest, hashes, SQLite integrity, and foreign-key checks. Never test restore by overwriting the live database directory.
5. Save the backup location, manifest, old commit SHA, intended target commit SHA, and test results in a local change record.

## Update the application

From the repository root, after confirming the working tree is clean:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
python run.py --dev --check-only
python -m pytest
```

- Use the dependency and test results from the exact target commit; do not skip a failing security audit or test suite.
- The launcher may install/update dependencies, prepare Ollama, or download a configured model if missing. Review [Development setup](../DEVELOPMENT.md) before running it on a machine where network access or package changes are restricted.
- Start the app with `python run.py` and perform the local smoke checks described in [Target-host acceptance baseline](../TARGET_HOST_BASELINE.md).
- Do not declare the update accepted based only on CI. Run the baseline on the actual target machine and retain the redacted report outside the repository.

## Rollback application code

1. Stop the application cleanly.
2. Check the local change record and confirm the exact previous commit SHA.
3. Return to the prior code revision:
   ```bash
   git switch --detach <previous-commit-sha>
   python run.py --dev --check-only
   ```
4. Start the application and verify local health, storage health, and the critical workflow that failed.
5. If the newer version changed database schemas, **do not assume code rollback alone is safe**. Do not overwrite live databases automatically. Restore the pre-update backup into a separate staging directory, validate it, and decide explicitly whether the application and data must both be rolled back. Use [SQLite backup and restore](SQLITE_BACKUP_RESTORE.md) and [Verified database backup and restore](../DATABASE_BACKUP.md).

## Restore safety rules

- Backup bundles may contain private prompts, source excerpts, assessment answers, settings, and task payloads. Keep them local and access-controlled.
- A successful staging restore is not permission to replace live data.
- Keep the original backup unchanged until the restored application has passed acceptance checks.
- Record the hashes and schema versions from the manifest; do not edit the manifest to force a restore.
- If integrity, foreign-key, schema, or application-health checks fail, stop and preserve the failed staging copy for investigation rather than attempting an in-place repair.
- Remote access, cloud execution, and provisioning remain disabled unless separately approved through the project's security and cost gates.

## Acceptance record

For every update, retain:

- previous and target commit SHAs;
- backup manifest and SHA-256 values;
- target-host baseline report;
- test and dependency-audit results;
- staging-restore result;
- operator decision to accept or roll back.

This runbook documents a controlled procedure; it does not itself prove that an update or rollback has been tested on a particular host.
