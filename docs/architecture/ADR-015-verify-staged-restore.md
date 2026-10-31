# ADR-015: Revalidate staged database snapshots before restore publication

- Status: Accepted
- Date: 2026-10-31
- Scope: verified multi-database backup restoration

## Context

`restore_databases()` verified each source backup file against the manifest hash and schema metadata, then later copied the source into a staging directory with SQLite's backup API. The copied snapshot was integrity-checked, but its hash and schema metadata were not compared with the already verified manifest. A valid SQLite backup could therefore change between preflight verification and the actual copy, allowing the restore to publish bytes different from the manifest-verified artifact.

The manifest format version also used equality checks without a strict integer-type check. In Python, `True == 1`, so a malformed boolean version could be accepted as legacy format version 1.

## Decision

- Keep the existing preflight hash, integrity, foreign-key, user-version, and table-list checks.
- After each database is copied into the restore staging directory, compare the staged snapshot's SHA-256, SQLite user version, and table list against the corresponding manifest entry.
- Abort the restore and remove the staging directory if any staged snapshot differs; never publish a partially verified destination.
- Require `format_version` to be an actual integer, rejecting booleans and floats before selecting the legacy or current manifest schema.

## Verification

Regression tests mutate a valid SQLite backup after preflight but immediately before the staging snapshot is copied. The restore must reject the mismatch and leave no destination or temporary restore directory. A separate test verifies that boolean manifest format versions are rejected. Existing valid legacy two-store and current three-store backup/restore tests remain unchanged.

## Consequences and limits

- The published restore now corresponds to the exact database bytes described by the manifest at the point of staged snapshot verification.
- No backup format, schema, or database migration changes are introduced.
- The manifest remains an integrity manifest, not a cryptographic signature or authenticity proof; an attacker able to replace both backup files and the manifest can construct a new internally consistent bundle.
