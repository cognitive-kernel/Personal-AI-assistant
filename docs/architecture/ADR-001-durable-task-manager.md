# ADR-001: Durable task manager boundary

- Status: Proposed
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
- Pause and cancellation are cooperative. Running tasks transition to a request state; workers settle that request at a checkpoint/control poll. A crashed worker's expired pause/cancel request is settled by the next claimant.
- Paused tasks retain their checkpoint and must be explicitly resumed.
- Priority is ordered descending among due tasks; scheduled tasks are not claimed before their due time.

## Safety boundaries

This module is a scheduler, not a sandbox. Any future code-execution task must run in a separately constrained laboratory with explicit filesystem, network, time, memory, and process limits. The task queue must never imply that a task's output is verified merely because its status is succeeded.

## Compatibility and migration

The task queue schema is owned by the versioned registry in `app/schema_registry.py`. `db.init_databases()` initializes or upgrades both SQLite databases through the migration runner; `initialize_task_store()` only validates the task table and required indexes. Before applying pending migrations to an existing database, startup creates a verified local backup bundle. Unknown schemas or backup failures stop initialization rather than attempting an ad-hoc `ALTER TABLE`.

## Acceptance gates

- Unit tests cover lifecycle, priority/scheduling, retry limits, checkpoints, pause/resume, cancellation, lease expiry/reclaim, and invalid JSON.
- CI must pass on the exact pull-request head before merge.
- Integration with FastAPI and real learning workers is not part of this increment and must be delivered separately with integration tests.
