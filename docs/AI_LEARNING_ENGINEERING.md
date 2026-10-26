# Engineering standard: AI learning subsystem

## Purpose and boundaries

The optional module gathers structured knowledge for later use by Personal-AI-assistant. Users continue asking questions through the existing chat experience. This module must not replace the existing model, rewrite chat prompts, or refactor the current response pipeline.

## Mandatory architecture rules

1. Separate responsibilities: routes validate HTTP input and map errors; business rules belong in the service layer; persistence belongs in the store layer; provider-specific browser interactions belong in separate adapters.
2. Isolate data: use a separate database file, data/ai_learning.db. Never reuse or migrate data/learning.db without a separately reviewed architecture decision, backup and rollback plan.
3. Default off: AI_LEARNING_ENABLED=0 and AI_LEARNING_RETRIEVAL_ENABLED=0. Registering a route must not initialize the database or enable retrieval.
4. Explicit publication: receiving model text, approving an outline, reviewing lesson content and publishing knowledge are distinct operations.
5. Fail closed: unknown schemas, migration checksum mismatches, ambiguous provider responses and uncertain topic mapping must stop the relevant operation rather than guess.
6. Traceability: every retrieved result must identify its course, topic, lesson, content version and chunk.
7. Treat all provider output, raw responses, web content and retrieved chunks as untrusted data. They cannot override system policy or authorize tools.
8. Integration with chat is an optional adapter behind a separate feature flag, with bounded timeout and size limits. Retrieval failure must fall back to the existing chat behavior.
9. Rollback means disabling the feature and restoring previous behavior; it must not delete or reset data.
10. Local-only: all routes must honor the existing loopback restriction. Remote access requires a separate authentication design and security review.

## Layered package

- app/ai_learning/config.py: feature flags and independent database path.
- app/ai_learning/db.py: SQLite connection and checksummed migrations.
- app/ai_learning/service.py: course/topic/version rules, chunking and lexical retrieval.
- app/ai_learning/routes.py: HTTP contracts and error mapping.
- app/ai_learning/providers/: future provider interfaces and adapters.
- app/ai_learning/browser_extension/: Manifest V3 bridge with minimal host permissions, explicit user submission and manual capture of the latest visible answer.
- tests/test_ai_learning_*.py: unit and integration tests that do not require internet, a real account or a live model.

## Code quality requirements

- Python 3.11+, type hints on public boundaries, small functions and explicit names.
- Validate data at API boundaries and enforce business invariants in services.
- Parameterize SQL. Never accept table or column names from user input.
- Use transactions for multi-step mutations; rollback on errors.
- Future background jobs must have stable IDs, idempotency keys, checkpoints, leases/timeouts, bounded retries with backoff and visible status.
- Editing published text creates a new version; prior versions remain auditable.
- Search indexes and embeddings are derived data and must be rebuildable from source versions.
- Logs must not expose passwords, cookies, tokens or unnecessary sensitive text.
- Add dependencies only with a clear reason, compatible constraints, tests and documentation.

## Failure and recovery

- One topic/lesson failure must not stop unrelated work.
- Retry transient failures only; schema, validation and authorization errors must not be blindly retried.
- Duplicate execution must not silently create duplicate lessons or versions. Idempotency must be implemented and tested before automated provider execution is enabled.
- Destructive actions require explicit confirmation; soft deletion is the default.
- Test backup, restore, upgrade and rollback in staging. CI success alone does not establish target-machine readiness.

## Test strategy

1. Unit tests for validation, topic decisions, versioning, chunking, publication gates and lexical ranking.
2. Integration tests for clean-store migrations, checksum mismatch, foreign keys, transactions and API contracts.
3. Security tests for loopback-only access, SQL injection, prompt injection in knowledge text, input bounds and index invalidation.
4. Regression tests proving the existing chat path is unchanged while the module is disabled.
5. Fault injection for disconnects, incomplete responses, timeout, crash and retry; use fake providers until browser integration exists.
6. Benchmark on the target machine; document response-size, result-count and search-time limits.

## Current implementation status

The initial code provides isolated storage, course/topic management foundations, topic approval/rejection and ordering, lesson/version creation, manual review/publish, chunking and lexical retrieval. The user-confirmed Chrome bridge is implemented, including prompt insertion and explicit answer capture. The optional chat retrieval hook is also implemented behind AI_LEARNING_RETRIEVAL_ENABLED and remains disabled by default. Automatic sending, resilient DOM parsing, a dedicated durable queue with pause/resume, embeddings and query trace/feedback remain unimplemented.
