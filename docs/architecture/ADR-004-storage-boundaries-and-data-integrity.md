# ADR-004: Storage boundaries, ownership, and data integrity

- Status: Accepted as the target architecture; implementation is incremental
- Date: 2026-10-15

## Context

The system needs durable knowledge, semantic and lexical retrieval, experience memory, task orchestration, and a graph of concepts/dependencies. Hardware is constrained today, but data loss and invasive migrations must not become the cost of scaling later.

The existing application currently has two SQLite connection targets (settings and learning) and several learning/evidence tables in the learning database. This ADR does not claim that five independent database engines already exist.

## Decision

Define five independently owned logical stores behind explicit repository interfaces. Each store owns its schema, migrations, validation, backup/restore policy, and retention policy. Other modules must use its interface rather than directly writing its tables.

1. **Knowledge store** — canonical source records, immutable content revisions, provenance, version identifiers, verification status, and concept metadata.
2. **Retrieval index store** — derived lexical and vector indexes. Every indexed item carries a stable source/revision ID and embedding/model/index version. Indexes are rebuildable from canonical records; they are never the sole copy of user-provided or learned content.
3. **Experience store** — append-oriented observations of attempted methods, context, input/output references, outcome, errors, evidence class, software/environment versions, confidence, and time-dependent evaluation. Experiences reference knowledge revisions and task IDs rather than copying full code/content.
4. **Task store** — durable task queue, idempotency key, status transitions, attempts, leases, checkpoints, cancellation/pause requests, result references, and audit events.
5. **Knowledge graph store** — typed edges between concepts, source revisions, versions, dependencies, experiences, and tasks. Edges carry provenance and validity/version context.

A sixth operational configuration/logging concern may be implemented where appropriate; it must not be forced into one of these five domains merely to keep the count at five.

## Physical deployment and hardware policy

Logical isolation is mandatory; five separate database server processes are not. Physical storage is chosen per domain through adapters:

- SQLite is an acceptable initial transactional engine for structured records and task state, provided each connection is configured safely and migrations/backups are tested.
- The retrieval index may begin as an embedded, local index and move to a dedicated vector/lexical engine if measured scale or quality warrants it.
- The graph may initially use typed relational edge tables; a graph-specific database is not a prerequisite. Adopt one only after representative graph queries and operational costs justify it.
- Store locations and engines are configuration, not hard-coded application assumptions.
- Adapters must make moving a domain to another engine possible without changing domain services or public API contracts.

This avoids unnecessary RAM overhead now without coupling the architecture to a single engine forever.

## Cross-store consistency

Do not assume a distributed transaction across stores. Use stable IDs, idempotent operations, an outbox/reconciliation pattern where cross-store updates must eventually converge, and explicit consistency states. For example, a canonical knowledge revision can commit first; indexing is then queued and marked pending until the lexical/vector indexes are updated. A failed index update must never delete or invalidate the canonical record.

Task completion and experience capture must also be idempotent. Replayed work must not create duplicate canonical experiences; use deterministic idempotency keys and content/context fingerprints where appropriate. Deduplication must not merge observations that differ materially in version, environment, input, or outcome.

## Versioning and migration rules

- Use numbered, ordered, transactional schema migrations; do not add ad-hoc startup-time ALTER statements as the long-term migration mechanism.
- Every migration declares its source/target schema version, preconditions, and rollback/forward-recovery strategy.
- Back up before destructive migration; verify backup readability and restore on a copy before relying on it.
- Prefer expand/migrate/contract: add compatible schema, backfill and validate, switch reads/writes, then remove obsolete schema in a separately reviewed change.
- Preserve stable IDs and provenance through migration. Never silently discard unknown fields, old versions, failed experiences, or source records.
- Keep checksums/counts and migration audit records; test migration from the oldest supported schema and from a realistic populated fixture.
- A backup is not verified until a restore test passes.

## Retrieval strategy

Default to hybrid retrieval only when the task benefits from recall: lexical retrieval for exact identifiers/error strings, vector retrieval for semantic similarity, graph traversal for prerequisites/dependencies, and experience retrieval for context-matched outcomes. Merge by stable record/revision IDs, deduplicate, rerank against task intent, version/environment compatibility, evidence quality, recency, and outcome. Do not force all four retrieval methods on every simple response; routing decides which sources are needed.

## Security and resource controls

All stores enforce size limits, input validation, access boundaries, and structured error handling. User-supplied documents and retrieved text are untrusted data, not executable instructions. Indexing and graph expansion must have budgets and cancellation points. Resource scarcity changes batch size, worker count, embedding model, and index strategy; it does not justify weakening durability or provenance.

## Acceptance gates before migration

1. Inventory existing SQLite tables and every read/write path.
2. Define versioned repository contracts and migration runner.
3. Add schema version metadata, backup/restore utilities, and migration tests.
4. Create populated fixtures and verify migration preserves row counts, IDs, hashes, foreign keys, and representative queries.
5. Only then migrate existing learning/evidence/knowledge tables into their owning repositories.
6. Add retrieval indexes as derived data and prove a full rebuild from canonical records.
7. Add graph storage after concept/edge contracts and use cases are tested.

## Consequences

This introduces clear boundaries and some interface/migration work upfront. It does not require five heavyweight services now. It reduces future migration risk through ownership, versioned schemas, reproducible indexes, and tested recovery rather than relying on a one-time split of live data.
