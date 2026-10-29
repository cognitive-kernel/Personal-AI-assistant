# ADR-001: Durable task manager boundary

- Status: Accepted
- Date: 2026-10-15
- Scope: First increment of the autonomous-system runtime

## Decision

Introduce a SQLite-backed task queue in app/task_manager.py that is independent of FastAPI routes and learning-worker implementation. It uses the existing learning database connection so task history remains local and persistent.

The queue owns task lifecycle and coordination only. It does not execute arbitrary payloads, invoke models, install packages, or access the network. Workers are responsible for dispatching allow-listed task types and checking cooperative controls.

## Task record contract

Each task stores:
- stable integer ID and a task type;
- JSON payload, priority, due time, retry limit, and attempt count;
- explicit lifecycle status;
- optional JSON checkpoint and result;
- bounded error text and timestamps;
- worker lease owner and expiry.

Payload, checkpoint, and result must be valid JSON; NaN and Infinity are rejected. Task records reference execution data instead of duplicating source files or knowledge content.

## Concurrency and recovery

- Claiming uses SQLite BEGIN IMMEDIATE and a single transaction to avoid two workers claiming the same ready task.
- A worker lease expires if the worker disappears. Expired running tasks can be reclaimed within their retry limit; exhausted tasks become failed.
- Workers must renew leases for long operations and may only mutate tasks while their lease is valid.
- If a task is claimed but the OS refuses to start its heartbeat thread, the handler must not run without lease renewal; the worker records a retryable infrastructure failure and releases the lease. If storage is also unavailable, lease expiry remains the recovery fallback.
- Pause and cancellation are cooperative. Running tasks transition to a request state; workers settle that request at a checkpoint/control poll. A crashed worker's expired pause/cancel request is settled by the next claimant.
- Paused tasks retain their checkpoint and must be explicitly resumed.
- Inactive tasks may be deleted through the loopback-only API; task-event rows cascade with the task. Running, pause-requested, and cancel-requested tasks cannot be deleted until their worker lifecycle settles. The dashboard requires explicit confirmation before deletion.
- Priority is ordered descending among due tasks; scheduled tasks are not claimed before their due time.

## Safety boundaries

This module is a scheduler, not a sandbox. Any future code-execution task must run in a separately constrained laboratory with explicit filesystem, network, time, memory, and process limits. The task queue must never imply that a task's output is verified merely because its status is succeeded.

## Compatibility and migration

The task queue is initialized idempotently through initialize_task_store() in the local learning database. FastAPI routes expose task lifecycle operations behind a loopback-only boundary, and the managed runtime is wired into application startup/shutdown with an explicit allow-listed handler registry. Startup reconciles learning jobs and enqueues them idempotently. Schema-version and fingerprint validation run before worker activation; the task table's idempotent initialization is not itself a claim that every database migration is generically reversible.

## Acceptance gates

- Unit tests cover lifecycle, priority/scheduling, retry limits, checkpoints, pause/resume, cancellation, lease expiry/reclaim, and invalid JSON.
- Worker regression tests also inject a transient failure while persisting a successful handler result and verify bounded retry, no premature result publication, and eventual durable completion.
- Worker regression tests also inject heartbeat-thread startup failure and verify that the handler is not executed and the claimed task is requeued with a bounded retry.
- CI must pass on the exact pull-request head before merge.
- Integration with FastAPI and allow-listed learning handlers is implemented and covered by repository CI. Target-host crash/restart, sustained-load, full task-type fault injection, and local Windows database acceptance remain separate release gates.
