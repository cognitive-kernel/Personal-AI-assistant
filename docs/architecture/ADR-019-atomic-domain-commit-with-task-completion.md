# ADR-019: Atomic Domain Commit with Durable Task Completion

- Status: Proposed
- Date: 2026-11-01
- Related work: PR #677 task-control checkpoints; PR #678 atomic completion follow-up
- Scope: durable task handlers whose domain state and task queue share the learning SQLite database

## Context

A cooperative checkpoint immediately before a domain write narrows, but does not eliminate, the pause/cancel race. A control request can commit after the final checkpoint and before the handler persists its domain result. The worker may then settle the queue row as paused or cancelled even though the domain mutation has already been written.

The knowledge-graph review handler exposed this boundary: the review report and edge status are stored before the task worker performs its separate `complete_task` transaction.

## Decision

Add an optional `before_commit` callback to `task_manager.complete_task`. The task manager invokes it only after verifying that the worker still owns a running task, while holding the same SQLite `BEGIN IMMEDIATE` transaction used to mark the task succeeded. The callback receives the active connection and returns the task result to persist.

Expose this contract through `TaskContext.complete_with_commit`. A handler using it commits its domain mutation, final checkpoint, task result, and terminal `succeeded` status in one transaction. `TaskWorker` recognizes the already-completed context and does not issue a second completion.

The knowledge-graph review service uses this path for accepted, rejected, conflicting, and inconclusive review outcomes. Its persistence callback performs only the local graph-row update; source retrieval and model inference remain outside the transaction.

The transaction defines a clear ordering:

- If pause/cancel obtains the SQLite write reservation first, the callback is not run; the control request is settled and no graph result is written.
- If atomic completion obtains the reservation first, the domain result and task success commit together; a later pause/cancel request observes a terminal task and cannot retroactively cancel the committed result.

## Constraints

- The callback must be short, synchronous, database-only, and use only the supplied connection. It must not perform network I/O, model inference, or open another database connection.
- Use this API only when the domain mutation and `task_queue` are in the same SQLite database and transaction.
- External side effects or writes to another database require an outbox/idempotency design; this callback does not make cross-system operations atomic.
- Cooperative checkpoints remain necessary between long-running operations. An in-flight HTTP request or model call is not forcibly interrupted.

## Verification

Regression coverage is added for:

- atomic domain callback + task result/checkpoint persistence;
- callback suppression when cancellation has already won;
- rollback of both task and domain changes when the callback fails;
- worker behavior when a handler completes atomically;
- knowledge-graph review success and pause/cancel at the final commit boundary.

No database schema migration is required.
