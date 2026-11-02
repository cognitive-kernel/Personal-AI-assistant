# ADR-019: Atomic Domain Commit with Durable Task Completion

- Status: Proposed
- Date: 2026-11-01
- Related work: PR #677 task-control checkpoints; PR #678 atomic completion follow-up
- Scope: durable task handlers whose domain state and task queue share the learning SQLite database

## Context

A cooperative checkpoint immediately before a domain write narrows, but does not eliminate, the pause/cancel race. A control request can commit after the final checkpoint and before the handler persists its domain result. The worker may then settle the queue row as paused or cancelled even though the domain mutation has already been written.

The knowledge-graph review handler, temporary-candidate validation, `experience.run_test`, and `factual.verify_claim` exposed this boundary. Their domain writes were stored before the task worker performed its separate `complete_task` transaction. Candidate rejection additionally scrubs temporary content and removes unverified domain rows; experience testing records a versioned evidence/result update. A late pause/cancel must not leave a task non-successful after its domain mutation has already committed.

## Decision

Add an optional `before_commit` callback to `task_manager.complete_task`. The task manager invokes it only after verifying that the worker still owns a running task, while holding the same SQLite `BEGIN IMMEDIATE` transaction used to mark the task succeeded. The callback receives the active connection and returns the task result to persist.

Expose this contract through `TaskContext.complete_with_commit`. A handler using it commits its domain mutation, final checkpoint, task result, and terminal `succeeded` status in one transaction. `TaskWorker` recognizes the already-completed context and does not issue a second completion.

The knowledge-graph review service uses this path for accepted, rejected, conflicting, and inconclusive edge outcomes. Temporary-candidate validation uses it for the final passed/test-review write and for the multi-source rejection/scrub operation. The controlled experience-test handler uses it to persist the versioned experience evidence/result update with task success; an expected-version check rejects stale results if the experience changed during source verification or sandbox execution. Factual claim verification uses it to persist the factual test record, any L1-to-L2 promotion, and task success together, rechecking claim identity and eligibility under the same write reservation. Source retrieval, model inference, semantic review, and sandbox execution remain outside the short final transaction.

The transaction defines a clear ordering:

- If pause/cancel obtains the SQLite write reservation first, the callback is not run; the control request is settled and no graph result is written.
- If atomic completion obtains the reservation first, the domain result and task success commit together; a later pause/cancel request observes a terminal task and cannot retroactively cancel the committed result.

## Constraints

- The callback must be short, synchronous, database-only, and use only the supplied connection. It must not perform network I/O, model inference, or open another database connection.
- Use this API only when the domain mutation and `task_queue` are in the same SQLite database and transaction.
- External side effects or writes to another database require an outbox/idempotency design; this callback does not make cross-system operations atomic.
- Cooperative checkpoints remain necessary between long-running operations. An in-flight HTTP request or model call is not forcibly interrupted.
- Experience source fetching and sandbox execution happen before the atomic commit. The expected-version guard prevents overwriting intervening experience edits; it does not make the separate settings-database sandbox-consent flag part of the learning-database transaction.

## Verification

Regression coverage is added for:

- atomic domain callback + task result/checkpoint persistence;
- callback suppression when cancellation has already won;
- rollback of both task and domain changes when the callback fails;
- worker behavior when a handler completes atomically;
- knowledge-graph review success and pause/cancel at the final commit boundary;
- candidate validation success with task success in one transaction;
- pause/cancel winning before either candidate pass persistence or rejection/content-scrub persistence;
- successful `experience.run_test` persistence and durable task success in one transaction;
- cancellation winning after the handler's preliminary control check but before the atomic commit, leaving experience history unchanged;
- rejection of a stale experience version when another update commits during sandbox execution;
- factual test record and L1-to-L2 promotion committed with task success;
- cancellation after the factual sandbox/control checks but before atomic commit leaves no factual test row or evidence-level promotion.

No database schema migration is required.
