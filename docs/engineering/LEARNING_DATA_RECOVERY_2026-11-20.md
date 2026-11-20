# Learning evidence recovery — 2026-11-20

## Scope

This follow-up extends the non-destructive learning-candidate recovery work on `fix/preserve-learning-evidence-main-20261120`. It does not promote rejected candidates, change their terminal status, or alter the user's live database.

## Root cause found

The legacy recovery utility attempted to restore candidate text from the durable `learning_jobs.lesson` only when the candidate's current `content` field was empty. A candidate with non-empty but corrupted content therefore bypassed the exact-hash recovery path, even when the durable lesson was still available and its SHA-256 exactly matched the candidate's immutable `content_sha256`.

## Change

- Recovery now verifies the current candidate content first.
- If current content is absent or hash-invalid, the utility checks the preserved job lesson.
- It restores that lesson only when its SHA-256 exactly matches the candidate's recorded content hash.
- A non-matching lesson is never substituted.
- Dry-run remains the default and performs no writes.
- Apply remains restricted to existing, unpromoted, terminally rejected candidates and uses a compare-and-set update keyed by candidate ID, content hash, failure reason, status, and promotion state.
- Apply now explicitly rolls back the transaction on any exception.
- The utility validates the presence of every table it queries before starting repair.
- A focused regression covers non-empty corrupted content, exact-hash restoration, and dry-run non-mutation.

## Validation

- Regression added: `tests/test_repair_legacy_corrupted_content.py`.
- Code/test commit: `ce4168c24e835c6aaf7733d72cca7413867c736b`.
- GitHub exact-code-commit checks were observed running: tests, Windows storage regressions, Browser E2E, and dependency audit. CodeQL job was cancelled by the workflow; no CodeQL pass is claimed.
- The following documentation-only commit records the recovery contract: `3ba945b26cbe21dddf4478d43e459c5995b361ad`.
- No local test execution, target-host database repair, or live user-data mutation is claimed.

## Acceptance criteria

1. Dry-run leaves candidate rows byte-for-byte unchanged.
2. Exact-hash matching durable lesson may replace corrupted candidate content.
3. A hash mismatch never overwrites candidate content.
4. Apply is idempotent and does not alter candidate terminal status or promotion state.
5. Any exception rolls back the whole apply transaction.
6. Rejected candidates remain ineligible for promotion.
7. Full CI must pass on the final code head before merge; the live database must be backed up and separately reviewed before any operator runs `--apply`.
