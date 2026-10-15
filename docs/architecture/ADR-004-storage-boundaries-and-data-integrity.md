# ADR-004: Storage boundaries, ownership, and data integrity

- Status: Accepted as the target architecture; implementation is incremental
- Date: 2026-10-15

## Context

The system needs durable knowledge, semantic and lexical retrieval, experience memory, task orchestration, and a graph of concepts/dependencies. Hardware is constrained today, but data loss and invasive migrations must not become the cost of scaling later.

The existing application currently has two SQLite connection targets (settings and learning) and several learning/evidence tables in the learning database. This ADR does not claim that five independent database engines already exist.

## Decision

Define as many independently owned databases as the system's actual data-domain and operational requirements justify; the count is deliberately not fixed. Each database has an explicit repository interface and owns its schema, migrations, validation, backup/restore policy, and retention policy. Other modules must use its interface rather than directly writing its tables. Multiple databases may initially use the same database engine and run on the same machine/server; separate database servers are not required.

Candidate data domains to assess independently include:

- **Settings/configuration** — application preferences, secrets references, and versioned operational configuration.
- **Knowledge and source archive** — canonical source records, immutable content revisions, provenance, version identifiers, verification status, and concept metadata.
- **Experience memory** — append-oriented observations of attempted methods, context, input/output references, outcome, errors, evidence class, software/environment versions, confidence, and time-dependent evaluation. Experiences reference knowledge revisions and task IDs rather than copying full code/content.
- **Operational reports and audit events** — searchable logs, metrics, and administrative history with retention controls.
- **Conversation archive** — conversation/message history, with privacy, retention, and deletion rules appropriate to user data.
- **Task orchestration** — durable queue, idempotency key, status transitions, attempts, leases, checkpoints, cancellation/pause requests, result references, and audit events.
- **Retrieval indexes** — derived lexical/vector indexes; each item carries stable source/revision IDs and embedding/model/index versions. Indexes are rebuildable and never the sole copy of canonical content.
- **Knowledge graph** — typed edges between concepts, source revisions, versions, dependencies, experiences, and tasks, with provenance and validity/version context.

This is a discovery checklist, not a requirement to create exactly eight databases. The implementation plan must decide which domains warrant separate databases, which subdomains need further separation, and which small/closely coupled domains can share a database without weakening ownership, security, retention, or recovery requirements.

## Physical deployment and hardware policy

Separate databases are created according to the data-domain inventory and documented decision; the number is not fixed. On current hardware, multiple databases can use one engine/runtime and reside on the same host. With SQLite, independently separated domains use separate database files rather than merely table prefixes in one file. With a server-based engine, they can be separate databases on one server instance. Any database can later move to another server without changing its domain service contract.

- Each independently deployed database has a separately configured connection, schema version, migration history, backup/restore unit, and health check. A documented exception is required when domains share a database.
- The retrieval database stores derived lexical/vector indexes and stable references to canonical records; the index can be rebuilt from source databases. Do not treat it as the canonical owner of user-provided knowledge.
- The graph database may initially use typed relational edge tables in its own database; a graph-specific engine is considered only after representative query benchmarks justify it.
- Store locations, database names, and engine settings are configuration, not hard-coded application assumptions.
- Repository adapters must allow moving one database to another engine or server without changing domain services or public API contracts.

This preserves the required database separation while avoiding five heavyweight server processes on constrained hardware.

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

1. Inventory existing database files/tables and every read/write path, including settings, knowledge, experience, reports/logs, conversation archive, task queue, indexes, and graph-like relationships.
2. Define repository contracts and a data-domain matrix documenting ownership, privacy, volume, access patterns, retention, recovery objectives, and whether each domain needs a separate database.
3. Add schema version metadata, a migration runner, backup/restore utilities, health checks, and migration tests for each database selected by that matrix.
4. Create populated fixtures and verify migration preserves row counts, IDs, hashes, foreign keys, provenance, and representative queries.
5. Create the selected database files/namespaces and migrate existing data using tested, restartable migrations; do not split data solely to reach a predetermined count.
6. Add retrieval indexes as derived data in their chosen isolated database and prove a full rebuild from canonical records.
7. Implement separate graph storage if the domain assessment and tested use cases justify it.
8. Verify cross-database idempotency, outbox/reconciliation, backup/restore, and failure recovery before enabling production writes.

## Consequences

This introduces clear boundaries and some interface/migration work upfront. It does not require five heavyweight services now. It reduces future migration risk through ownership, versioned schemas, reproducible indexes, and tested recovery rather than relying on a one-time split of live data.
