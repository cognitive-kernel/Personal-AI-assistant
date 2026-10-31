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
- app/ai_learning/browser_automation.py: visible-UI Playwright worker, provider-gate classification, and persisted-run recovery.
- app/ai_learning/evaluation.py: deterministic, dependency-free retrieval/answer regression metrics.
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

The initial code provides isolated storage, a dedicated management page, course/topic management, topic approval/rejection and ordering, lesson creation and title editing, versioned content, manual review/publish, chunking and lexical retrieval. The user-confirmed Chrome bridge is implemented, including prompt insertion, a baseline check to reduce accidental capture of an older answer, and explicit answer capture. The optional chat retrieval hook is implemented behind AI_LEARNING_RETRIEVAL_ENABLED and remains disabled by default. The dashboard and extension assets are included as package data for installed builds. The isolated SQLite store has a persisted global pause flag, idempotent run IDs, and recovery for queued/interrupted runs. Every explicitly selected lesson topic is persisted as its own FIFO run before browser work starts; the worker no longer keeps remaining selected topic IDs only in memory. Manual continuation and recovery of legacy lesson runs also materialize the remaining approved topics into SQLite before starting the worker, but only when no runnable same-course queue already exists. Paused topic runs remain durable but are excluded from dispatch and automatic recovery until the topic is unpaused, which wakes the FIFO dispatcher. When the feature is enabled, application startup initializes the AI-learning store and dispatches the oldest runnable persisted run unless global pause is active; each worker drains the next runnable persisted run after completion. Recovery reuses the saved prompt but may have to request the remote answer again. A process-wide SQLite lease with a unique owner token, fencing epoch, 90-second expiry and 15-second heartbeat now serializes browser work across application processes sharing the same local AI-learning database and Chrome profile. The worker checks persisted pause/cancel/topic state and refuses late writes after lease loss. This remains an in-process browser thread and is not a multi-host distributed queue; exact remote-session continuation is not implemented. Semantic duplicate checks use an optional local Ollama embedding model and fail closed when required embeddings are unavailable. Deterministic offline quality metrics cover retrieval hits, forbidden IDs, Persian normalization, concept coverage, forbidden claims, and source-link coverage; these metrics do not prove factual correctness. The search UI supports helpful/not-helpful feedback tied to a trace ID. Trace rows store a one-way query hash and selected chunk IDs, not raw query text; feedback comments are bounded and aggregate reporting does not expose comments. Provider parsing remains DOM-sensitive because automation is intentionally restricted to the visible UI.


## Current pause/resume behavior

The management dashboard exposes global pause and resume controls. The pause flag is stored in the isolated AI-learning SQLite database. A live worker pauses while waiting for a response and lesson generation checks the flag between batches. When the module is enabled, application startup automatically dispatches the oldest persisted queued/interrupted run unless the global pause flag is active. The dashboard's resume/recovery action remains available for manual recovery and to continue approved topics that do not yet have substantive lesson content. Responses already committed remain saved; a response still only visible in ChatGPT may need to be requested again. This cooperative mechanism is not an OS-level interrupt and does not guarantee exact continuation of the remote ChatGPT generation itself.


## Dashboard controls and lesson integrity (2026-10)

- Topic cards show per-topic progress and the course-level aggregate, with periodic refresh. Select-all and approve-all apply to the selected course; approval remains explicit and is not performed automatically after outline capture.
- Approved topics can be selected and submitted for lesson capture in groups of at most 100. Per-topic and per-lesson pause/resume controls preserve saved content. Global pause is persisted in SQLite. On startup the worker automatically dispatches the oldest queued/interrupted run unless global pause remains active; the dashboard also exposes a manual resume/recovery action.
- Each topic exposes its lessons, version history, captured Markdown and raw response, and source links. Sources extracted from ChatGPT output are stored in the isolated `sources` table with `unverified` status until checked. A link appearing in a lesson is not proof that its contents were consulted.
- Lesson versions with blank or insubstantial content must not be accepted as completed. Bulk review skips versions with no substantive body. Editing creates a new content version, preserving history. A mistakenly published version can be unpublished without deleting its content.
- The destructive reset control requires typing `RESET`, preserves course definitions, and clears captured topics, lessons, versions, source rows (through foreign-key cascades), run history, chunks and query traces. It refuses to run while the browser worker is active.
- The queue remains single-worker, but a durable SQLite owner lease, fencing epoch and heartbeat now coordinate processes sharing the same local database and browser profile. A process cannot requeue a `running` record while another process holds a valid lease; after expiry, recovery may reclaim the record. This does not coordinate separate hosts or network filesystems. A prompt interrupted before its response is committed may be reissued on recovery. The Chrome extension is retained because it remains a documented fallback and is covered by permission/packaging tests; no legacy code was removed without evidence that it was unused.


## Knowledge validation policy

The proposed methodology for evidence-based knowledge validation, with a primary focus on IT and programming, is documented in [AI_LEARNING_KNOWLEDGE_VALIDATION.md](AI_LEARNING_KNOWLEDGE_VALIDATION.md). This is documentation only: it does not implement automatic factual adjudication, alter publication gates, or treat user usefulness feedback as truth evidence.

## AI-learning status and acceptance

See [AI_LEARNING_STATUS.md](AI_LEARNING_STATUS.md) for the implementation checklist, automated CI evidence, known limitations, and the remaining target-machine checks. Query feedback is available through the search UI and the loopback-only API. It is a usefulness signal, not a verification signal: feedback must never mark a source or lesson as factually verified. A new migration stores one replaceable feedback record per retrieval trace; deleting a trace cascades its feedback. Do not expose this API beyond loopback without a separately reviewed authentication and abuse-control design.

## Process-restart retrieval regression

`tests/test_ai_learning_retrieval_boundary.py::test_published_knowledge_is_retrievable_after_a_new_process` verifies persistence across a fresh Python interpreter, not merely across requests in one process. The test writes a reviewed/published version and a separate unpublished draft to the isolated SQLite database, starts a subprocess with the same `AI_LEARNING_DB_PATH`, calls the production `integration.retrieve_context` adapter, and asserts:
- the published version is returned;
- the unpublished draft is not returned;
- the subprocess records a query trace in the same database.

The test uses deterministic fixture embeddings and makes no Ollama/model request. It proves SQLite persistence and the published-only retrieval boundary; it does not prove answer quality, factual correctness, or live-model behavior.
