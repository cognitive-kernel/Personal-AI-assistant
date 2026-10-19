# Final clean-start and learning-data acceptance

## Purpose

All current local learning content is disposable test data. It must not be promoted, copied, or described as trusted knowledge when the project is released. A successful release must demonstrate that the application can start with clean stores and acquire new knowledge through the reviewed learning pipeline.

This is a **release-stage operation**, not a development troubleshooting step. Do not clear databases while implementation, evaluation, or migration work remains in progress.

## Required safety rules

- Never delete or truncate a database to bypass `UntrackedSchemaError`, a failed migration, a failing test, or a startup failure.
- Keep existing test databases and pre-migration backups intact during development. They are useful for regression testing and recovery even though their knowledge is not trusted.
- Do not copy test knowledge, test sources, test feedback, generated lessons, embeddings, task payloads, or assessment results into the release stores.
- Preserve versioned schema metadata and required structural/default rows. “Empty” means no user-generated/test learning content, not a zero-byte SQLite file or a database without migration history.
- Settings required to run the application may be recreated from documented defaults. Secrets and local model configuration must be handled separately and must not be committed.
- Do not claim the clean-start gate passed until a fresh disposable-store rehearsal and the post-reset acceptance suite both pass on the target machine.

## Release procedure

1. Freeze the release candidate and record its exact Git commit and dependency versions. Stop the application and background workers.
2. Save a verified backup of all configured stores **outside the repository**. Record file hashes and verify SQLite integrity and foreign keys. Keep the backup until release acceptance is complete.
3. Use new, empty, disposable database paths for the rehearsal. Initialize them through the application's registered migrations; do not stamp schema versions manually and do not copy existing database files.
4. Start the application against those stores. Confirm the health endpoint, dashboard, settings defaults, learning curriculum, task queue, research workflow, and UI/API error handling. A missing optional local Ollama service must be reported as a dependency state, not as a generic dashboard-wide `Failed to fetch`.
5. Run the complete automated test suite and the required live-model evaluation. Human reviewers must assess factual correctness, source attribution/support, conflicting sources, language consistency, uncertainty, and prompt-injection resistance. A model's self-review or lexical score is not independent verification.
6. Verify that user-provided corrections remain untrusted until the configured review/verification process explicitly approves them. Verify provenance and version history for every item that can become trusted knowledge.
7. Once all acceptance gates are approved, create the final release stores using the supported initialization/migration path and verify that test content is absent. Do not perform ad-hoc SQL truncation across foreign-key-related tables. Use a reviewed, tested reset mechanism if one is added; until then, create fresh stores rather than destructively editing live files.
8. Run the smoke tests again against the final stores, archive redacted evidence outside the repository, and record the final commit. Keep the verified pre-release backup until the release is accepted.

## Evidence required

- release commit SHA and dependency lock/effective versions;
- backup manifest and successful integrity/foreign-key validation;
- schema fingerprints and migration histories for the fresh stores;
- proof that test knowledge, sources, feedback, lessons, assessments, and learning jobs are absent;
- health/API/UI smoke-test output and complete test results;
- live-model evaluation report and completed human-adjudication worksheet;
- explicit acceptance of outstanding security, recovery, and UI gates.

## Current status

**Not yet accepted.** Existing databases and model evaluation are development artifacts. Passing SQLite integrity checks only shows structural file integrity; it does not establish knowledge correctness or release readiness.
