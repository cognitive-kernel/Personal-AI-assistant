# ADR-023 — Dual fencing for learning-job execution

- Status: Proposed; exact-head CI validation pending.
- Date: 2026-11-21
- Scope: `learning.run_job` queue execution and mutations to `learning_jobs`.
- Depends on: ADR-022 (durable task lease fencing).

## Context

Queue-level fencing alone is insufficient when a domain job has its own status row. A task can be reclaimed while the domain row still says `running`; a second task must not reset that row while the first queue lease is still valid. Conversely, a stale worker must not commit lesson content, evidence, quality metadata, or a failure status after a replacement execution has taken ownership.

## Decision

1. Add `execution_owner`, monotonic `execution_epoch`, `execution_task_id`, and `execution_task_epoch` to `learning_jobs` through additive migration 49. Existing jobs remain intact and start at epoch 0.
2. Claim the domain job and advance its epoch under `BEGIN IMMEDIATE`; when called by a queue worker, validate the exact `learning.run_job` task type, payload, owner, and queue epoch in the same transaction.
3. Every domain write transaction validates both the queue lease and domain execution epoch before changing job state or persisting generated lesson/evidence/candidate records.
4. Completion and failure updates use compare-and-set predicates over job ID, running state, execution owner, and execution epoch.
5. Persist the originating queue task ID and lease epoch on the domain job. A duplicate task may not reset a running job while that exact queue lease remains active. It can recover the job only after the recorded queue lease has expired, been reclaimed, or settled.
6. Follow-up job creation, path advancement, task enqueue, and escalation writes performed by a learning worker validate the same authority inside each write transaction. Task creation can reuse the caller's SQLite connection so queue inserts participate in the same transaction as the ownership check.
6. Direct/manual execution receives a unique non-reusable owner token. A direct execution with no queue lease is not silently stolen by a duplicate queue task.

## Invariants

- Queue authority and domain authority must both be current for a queued learning job to write.
- Queue and domain checks occur inside the same SQLite transaction as the protected domain mutation.
- Reclaim increments the domain epoch; stale execution epochs cannot complete or fail the new run.
- A duplicate task cannot mark a currently owned learning job queued.
- A stale worker cannot record an escalation or failure after losing queue authority.
- A stale worker cannot create a follow-up learning job, advance a researched path, or enqueue the next task.

## Recovery

The task queue remains the durable retry authority. On a reclaimed task, the handler checks the recorded queue task ID/epoch. If the prior owner is no longer live, it resets the domain job to queued without deleting lesson/source/evidence data. The next claim increments the domain epoch and resumes from the durable queue checkpoint. If the prior owner is still live, the duplicate handler exits without modifying the domain job.

## Validation

Required:
- Same worker-label stale domain epoch cannot mutate after reclaim.
- Exact task type and payload are required to claim the domain job.
- Duplicate queue task cannot reset a live domain execution.
- Queue lease expiry/reclaim permits recovery and increments the domain epoch.
- Existing learning generation, retry, escalation, evidence-retention, and queue recovery tests pass on the exact PR head.

Target-host model-provider tests, long-running worker crash/restart, and Windows service acceptance remain separate. No live model evaluation or target-host test is claimed by unit tests.

## Residual scope

This decision covers the `learning.run_job` path and its lesson/evidence commit. Other background-work domains must be audited for equivalent transaction-level queue fencing; the UI module independence and end-to-end fault-isolation gates remain open under Issue #290.
