# ADR-020: Durable sequential learning-method orchestration

- **Status:** In Progress; runtime method orchestration, fallback, Experience observation, and execution fencing implemented; exact-head validation pending
- **Date:** 2026-11-13
- **Decision owners:** Repository maintainers

## Context and root cause

Historically, researched curriculum topics entered the Basic Learning job lane directly and a failed quality gate could create another correction job in the same lane. The current method-scoped coordinator replaces that behavior with the required fallback sequence:

1. AI Learning
2. Basic Learning
3. Topic/Course Learning

The existing `learning_topic_method_attempts` table (migration 51) is append-only but models mutable lifecycle states such as queued/running/failed in a single row. Its update/delete triggers prohibit lifecycle transitions, while its unique key allows only one row per method/attempt. That is not a valid append-only event model.

The AI Learning method now uses its own provider/model profile (ai_learning_llm_provider) and is enabled by default. Basic Learning and Topic/Course Learning use separate profiles. The visible Playwright/Chrome AI-learning console is a distinct optional interface, also enabled by default; its browser authentication/session state must never be treated as proof that a method job completed.

## Decision

### Ownership boundaries

- **Curriculum coordinator:** owns the topic-level state machine, current method, current learning job/run reference, next action, and advancement decision. It is the only component allowed to advance the topic gate.
- **Method adapters:** one adapter per method translates the coordinator contract into the relevant subsystem. AI Learning, Basic Learning, and Topic/Course Learning must not import or mutate each other's internal storage.
- **Attempt event ledger:** stores immutable lifecycle events and evidence. It does not execute learning or decide whether content is correct.
- **Independent evaluator:** evaluates the resulting immutable content version and returns a validated verdict. The coordinator consumes the verdict; it does not ask the generator to certify itself.
- **Experience System:** observes and indexes the outcomes of all three methods as experience evidence. It is not a fourth learning method and cannot independently advance a gate.

### Required order and transitions

Each topic/content-version pair uses the fixed order `ai_learning -> basic_learning -> topic_learning`. A method is attempted at most once per method slot unless a separately approved, bounded retry policy is introduced. A successful independent evaluation ends the sequence. A failed, unavailable, or inconclusive method advances to the next method only after its outcome is durably recorded. Failure of the third method leaves the topic `blocked/needs_review`; no later topic may start.

A generated lesson is never a pass by itself. Empty or malformed output, an unexecuted evaluator, an unavailable verifier, and an unproven result are not successful outcomes.

### Append-only event model

Migration 52 adds `learning_topic_method_attempt_events`. Each lifecycle transition is a separate immutable event with:

- path topic, method, attempt number, and per-attempt event sequence;
- status, unique idempotency key, and canonical payload hash;
- optional learning job or external AI-learning run reference;
- content hash, score, diagnosis, evidence, and timestamp.

The ledger enforces the transition order and rejects event-key reuse with different payloads. Repeated delivery of the same event is an idempotent replay. UPDATE and DELETE are rejected by database triggers. The legacy migration-51 table is retained for compatibility/history and is not the lifecycle event store.

### Failure and recovery contract

- Persist the method's queued/running/result event before publishing dependent work.
- Use a stable dedupe key derived from topic, content version, method, and transition.
- Reconcile durable coordinator state with the owning subsystem after restart; never infer success from an in-memory worker or missing task.
- AI Learning disabled, browser unavailable, login/authentication required, or explicit user action required must be recorded as `unavailable` with a safe reason, then fall through to Basic Learning. Do not bypass authentication or browser security controls.
- A method result must be fenced by the current topic owner and content hash. Stale workers cannot overwrite or advance a newer attempt.
- Evaluator outage is not a pass. Record the failure/inconclusive evidence and follow the configured fail-closed recovery policy.
- Preserve every generated version and source record. Corrections create new linked versions; no method may overwrite prior lesson content.

## Runtime implementation status

Implemented:
- Migration 51 introduced method metadata; migration 52 added the append-only lifecycle event ledger; migration 53 added method-scoped job identity; migration 54 added execution owner and monotonic epoch fields.
- `app/learning_topic_method_attempts.py` validates the fixed method order, event idempotency, transition ordering, and concurrent replay.
- `app/learning_method_orchestrator.py` starts the first enabled method, records disabled/unavailable methods, advances after permanent generation or evaluation failure, and blocks after the third method.
- `learning.run_job` maps AI Learning, Basic Learning, and Topic/Course Learning to their independent provider/model settings.
- `app/learning_execution.py` fences job claims, failures, cancellations, and commits by owner plus execution epoch; `learning_tasks` passes the live task context and checkpoints around execution.
- The Experience System exposes a read-only observation endpoint over the immutable event ledger; it cannot mutate coordinator state.
- Focused tests cover fallback after permanent job-creation failure, user-disabled AI Learning, concurrent job creation/claim, stale execution epochs, append-only events, and observer read-only behavior.

Remaining validation:
- Run the exact-head CI suite and resolve every failure without removing or weakening tests.
- Add/complete crash-after-event-before-enqueue, crash-after-evaluation-before-advance, lease-loss during provider inference, stale-worker final-commit, and full three-method end-to-end scenarios.
- Validate each provider adapter against configured Ollama and OpenAI-compatible endpoints; no remote model execution is claimed by source-level tests.
- Confirm the separate visible-browser subsystem's recovery and user-authentication behavior; it is not the AI Learning method executor.

Method-specific failures now advance to the next method; the legacy same-lane correction behavior remains only for non-method-scoped legacy jobs. The three-method workflow is implemented in the runtime path but is **not yet accepted as complete** until the remaining failure/recovery tests and exact-head CI pass.

## Acceptance tests

1. AI Learning enabled and browser available: AI run is created once, completed content is hash-bound, independently evaluated, and only a passing result advances the topic.
2. AI Learning disabled/unavailable/needs user action: an immutable unavailable event is recorded and Basic Learning starts exactly once.
3. Basic Learning failure or inconclusive evaluation: Topic/Course Learning starts exactly once using the persisted research evidence for that topic, not a replay of the Basic prompt.
4. Third-method failure: the topic becomes blocked/needs_review and the next topic remains unqueued.
5. Duplicate task delivery and concurrent workers: one transition and one next method are committed; stale workers cannot advance.
6. Crash after event persistence but before task publication: reconciliation publishes the same idempotent task without consuming a second method attempt.
7. Crash after evaluation persistence but before gate advancement: replay consumes the stored evaluation and performs one idempotent transition.
8. Content changes after scheduling: hash mismatch blocks stale evaluation and preserves both versions.
9. Evaluator unavailable or self-verifying: no pass is recorded.
10. Experience System observes each method outcome without changing coordinator state.

All tests must preserve existing task, migration, browser-learning, provenance, and user-data contracts. GitHub CI must pass on the exact PR head before target-host validation is requested. This ADR does not authorize merge or production execution.
