# ADR-017: Reconcile durable learning tasks during runtime maintenance

- Status: Accepted
- Date: 2026-10-31
- Scope: consistency between learning-job records and the persistent task queue

## Context

Creating a learning job and inserting its `learning.run_job` task are separate SQLite transactions. Startup reconciliation repairs the crash window when the process exits between those writes. However, if task insertion fails transiently while the process remains alive, the learning job can remain queued without a task until another application restart. The learning runtime already has a periodic maintenance callback, but it previously did not reconcile this queue boundary.

## Decision

- Invoke the existing idempotent `reconcile_queued_learning_tasks()` from the learning runtime maintenance callback as well as application startup.
- Isolate reconciliation failures with logging so factual and browser-learning maintenance still runs in the same cycle.
- Let the existing maintenance interval bound the normal retry delay; do not add a second queue, retry loop, or new schema.
- Preserve the existing bounded recovery policy for terminal task records and explicit cancellation semantics.

## Verification

- An integration regression creates a learning job without its queue task, runs the maintenance callback, and verifies that exactly one matching durable task is created across repeated cycles.
- A failure-injection regression verifies that a reconciliation exception does not prevent unrelated maintenance work from running.

## Consequences and limits

- A transient enqueue failure can be repaired without requiring an application restart.
- The job/task writes are still not one atomic transaction; this decision adds periodic repair, not a transactional outbox.
- If the learning database remains unavailable, reconciliation logs the failure and retries on a later maintenance interval. The delay is bounded by the configured maintenance interval during normal runtime.
