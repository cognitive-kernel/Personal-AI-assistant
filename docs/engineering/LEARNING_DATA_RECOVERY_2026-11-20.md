# Learning evidence recovery — 2026-11-20

## Scope

This follow-up extends the non-destructive learning-candidate recovery work on `fix/preserve-learning-evidence-main-20261120`. It does not promote rejected candidates, change their terminal status, or alter the user's live database.

## Root causes found

1. The legacy recovery utility attempted to restore candidate text from `learning_jobs.lesson` only when candidate `content` was empty. Non-empty but corrupted content bypassed exact-hash recovery even when the durable lesson still matched the candidate's immutable `content_sha256`.
2. The SQL filter selected only candidates with empty content or missing metadata. A hash-invalid non-empty content field could therefore remain invisible when source and quality metadata were already populated.

## Change

- The utility scans all eligible, unpromoted, terminally rejected candidates and skips records whose content hash and metadata are already complete.
- If current content is absent or hash-invalid, it checks the preserved job lesson and restores it only when its SHA-256 exactly matches the candidate's recorded content hash.
- If non-empty content is hash-invalid and no exact-hash lesson exists, the record is reported as a mismatch and no adjacent metadata or recovery marker is written.
- A non-matching lesson is never substituted.
- Dry-run remains the default and performs no writes.
- Apply uses a compare-and-set update keyed by candidate ID, content hash, failure reason, status, and promotion state.
- Apply explicitly rolls back the transaction on any exception.
- The utility validates the presence of every table it queries before starting repair.
- A focused regression covers corrupted non-empty content even when source and quality metadata are already complete, exact-hash restoration, and dry-run non-mutation.

## Validation

- Regression: `tests/test_repair_legacy_corrupted_content.py`.
- Latest validated code/test head: `b3c1c9df3ba73ac854461754631e77cc03d74dc2`.
- Initial checks on the earlier code commit showed tests, Windows storage regressions, and Browser E2E still running; dependency audit passed; CodeQL was cancelled. Those results do not validate the later code commit.
- Final code head requiring exact-head CI: `26e16eaecbcf267e6c30404f1fda28da3a5d5a6a`.
- No local test execution, target-host database repair, or live user-data mutation is claimed.

## Acceptance criteria

1. Dry-run leaves candidate rows unchanged.
2. Exact-hash matching durable lesson may replace corrupted candidate content, even if other metadata is complete.
3. A hash mismatch never overwrites candidate content; independently verifiable source/quality metadata may be restored without changing the content digest.
4. Apply is idempotent and does not alter candidate terminal status or promotion state.
5. Any exception rolls back the whole apply transaction.
6. Rejected candidates remain ineligible for promotion.
7. Full CI must pass on the final code head before merge; the live database must be backed up and separately reviewed before any operator runs `--apply`.
