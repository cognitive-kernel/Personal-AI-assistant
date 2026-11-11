# ADR-019: Research-first local learning curricula

- **Status:** Proposed — amended for unbounded, version-aware curriculum generation
- **Date:** 2026-11-10
- **Decision owners:** Repository maintainers

## Context

The primary `learning-form` accepted a topic and created one lesson job. It did not first research the subject, produce a comprehensive ordered syllabus, or coordinate lesson generation as a course. A separate preset curriculum catalog exposed fixed topics, while the Chrome/ChatGPT learning flow had a different outline-first interaction. The user requires the primary local learning engine to accept only a topic or lesson name, discover the subject's scope from public web sources, build the fullest useful ordered outline, and teach its lessons sequentially with `gpt-oss:20b`.

## Decision

1. The primary learning form creates a durable `learning.plan_curriculum` task; it does not enroll the user in a built-in curriculum.
2. The task searches multiple complementary web queries and requires at least three usable HTTPS pages from at least two domains. Search failure must not silently fall back to model-only syllabus generation.
3. The configured learning model generates an unbounded ordered curriculum through resumable batches of 8–40 topics (the final batch can be smaller). The batch size is a per-inference resource control only; there is no total topic, lesson, or batch limit. The default learning model is `gpt-oss:20b`; chat and programming model settings remain independent. Each batch and its single JSON-repair attempt have a bounded 4,096-token output allowance and request an 8,192-token context; the runtime RAM/model limit must satisfy it or the current batch fails without truncation.
4. Persist the complete outline, its SHA-256 digest, the model name, model-selection provenance, and bounded source provenance in `learning_path_research` (migration 42). Migration 43 adds per-topic hierarchy, source URL references, and structured applicability metadata.
5. Persist every outline topic in order in `learning_path_topics` as the durable course queue, but enqueue only the first execution job. A successful lesson completion creates the next execution job. A failed, rejected, or cancelled lesson blocks automatic advancement until an explicit resume action. This prevents parallel generation and preserves lesson history.
6. Remove preset curriculum enrollment controls from the primary UI. Keep legacy curriculum APIs and existing paths/data for backward compatibility; do not delete prior user data.
7. The Chrome/ChatGPT browser-learning feature remains separate and unchanged.

## Version-applicability contract

Version-sensitive technical material must keep separate dimensions instead of treating a product version as one universal compatibility switch. For SQL Server, for example, SQL Server 2025 is Database Engine 17.x with default database compatibility level 170, while SQL Server 2022 is Engine 16.x with default level 160. Database compatibility level controls selected T-SQL behavior and query-optimization behavior; it does not gate every new T-SQL syntax feature. The lesson must therefore record both engine version and database compatibility level, plus edition, platform, build/update, execution mode, and relevant prerequisites when the command depends on them. See the [Microsoft compatibility-level reference](https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-database-transact-sql-compatibility-level) and [compatibility certification guidance](https://learn.microsoft.com/en-us/sql/database-engine/install-windows/compatibility-certification).

## Consequences

- Curriculum generation is checkpointed after every batch and recoverable through the existing durable task queue. Each lesson stores product/version/build, edition, compatibility level, platform, execution mode, prerequisites, permissions, limitations, and exact source references where applicable; unsupported or undocumented scope remains explicitly `unverified`. Source publication/update dates are recorded only when available, while retrieval timestamps are recorded independently.
- Every generated course can show the web references used to build its outline, without representing those references as independent scientific verification.
- The configured learning model is pinned; cost/quality profiles may reject it but cannot silently replace it with another model.
- The local bootstrap installs and verifies both the general model and the dedicated learning model.
- Sequential generation may take longer than bulk parallel generation, but lesson order and per-course serialization are deterministic.

## Validation and residual risk

Automated tests must cover checkpoint recovery, multi-batch generation beyond 40 topics, source diversity, strict outline validation, per-topic version/source metadata, idempotent persistence, one-at-a-time queue advancement, failure blocking, model pinning, settings migration, and UI removal of preset enrollment. GitHub Actions must pass on the exact PR head. The user's Windows host (32 GB RAM, Core i5-12400, integrated graphics) still requires a real run to verify model loading, RAM use, inference latency, and Persian/English lesson quality. No merge is permitted until the repository's required host-validation gate is satisfied.
