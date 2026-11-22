# ADR-022 — Durable task lease fencing and queue ownership

- Status: Proposed; implementation is under exact-head CI validation.
- Date: 2026-11-21
- Scope: SQLite-backed durable task queue and its worker runtime.
- Supersedes: none.

## Context and root cause

The task queue previously identified a running owner only by `lease_owner` plus an unexpired timestamp. A worker process could be reclaimed after its lease expired, and a later worker could reuse the same worker identifier. In that case, the old process could still satisfy the owner-string check and mutate the new worker's checkpoint, result, retry state, or terminal status.

The queue implementation also combined data contracts, serialization, persistence queries, lease lifecycle, and user controls in one 645-line module. This made it difficult to audit ownership invariants independently from ordinary queue operations.

## Decision

1. Every successful claim increments a durable, monotonic `lease_epoch` in the same `BEGIN IMMEDIATE` transaction that assigns the owner and expiry.
2. Every worker mutation validates task ID, owner token, lease epoch, active status, and expiry while holding a write transaction. A stale epoch is rejected before any checkpoint/result/status mutation.
3. Each TaskWorker instance receives a unique opaque owner token. A restarted worker cannot inherit authority merely by reusing its human-readable worker label.
4. TaskContext carries the claim's epoch and supplies it to checkpoint, control, heartbeat, completion, and failure operations.
5. Queue responsibilities are separated into:
   - `task_manager_core.py`: shared data contracts and serialization.
   - `task_queue_store.py`: creation, lookup, pagination, and event reads.
   - `task_lease_manager.py`: atomic claim, lease validation, checkpoint, completion, and failure.
   - `task_control_manager.py`: user controls and scheduling.
   - `task_manager.py`: stable compatibility facade.
6. Schema change is additive: migration 48 adds a non-negative epoch with default 0 and an owner/epoch lookup index. Existing rows and checkpoints are retained; the first new claim advances the epoch to 1.

## Invariants

- A worker may mutate a task only while its owner token and epoch both match the current durable row and its lease is unexpired.
- Ownership validation and mutation are serialized in one SQLite write transaction.
- Reclaim increments the epoch before the new worker receives the task.
- A stale worker cannot renew, checkpoint, complete, fail, or read cooperative control state for a reclaimed lease.
- Process death releases OS resources; durable recovery remains based on persisted checkpoints and lease expiry, not in-memory worker state.
- The public import path remains `app.task_manager`; callers do not import storage internals unless they explicitly need a lower-level test seam.

## Failure and recovery

A worker that exits without settling a task leaves the durable checkpoint and lease record intact. After expiry, the next claim increments the epoch and receives the last committed checkpoint. All subsequent writes from the old epoch fail closed. User pause/cancel state is still settled by the existing lease-expiry reconciliation path.

## Validation

Required on the exact PR head:
- Same-worker-label reclaim test proving stale epoch cannot mutate the new lease.
- Worker-instance token uniqueness.
- Existing queue, retry, pause/cancel, process-restart, and crash-recovery tests.
- Full unit suite, Windows storage regression, Browser E2E, sandbox integration, CodeQL, and dependency audit where configured.

Target-host Windows service/ACL, long-running soak, and actual crash/restart checks remain separate acceptance evidence and must not be reported as passed by repository CI.

## Residual scope

This ADR establishes queue-level fencing. Domain transactions that mutate learning jobs, method attempts, or quality gates must additionally validate the exact task type and payload authority inside their own database transaction. That integration is a separate required acceptance item; this ADR does not claim that every domain write is already fenced.


## 2026-11-22 addendum — Domain transaction fencing

The queue-level lease contract above is the baseline, not proof that domain records are automatically protected. Draft PR #837 applies the second layer to task-owned domain mutations:

- Each domain adapter validates the exact task type and payload through the current TaskContext inside the same BEGIN IMMEDIATE transaction as its write.
- Parent-to-child queue insertion validates the parent lease in task_queue_store.create_task before inserting the child.
- Covered domains in the integration branch: learning jobs/evidence/budgets/escalations, researched curriculum source/path persistence and child lessons, factual verification, knowledge lifecycle/graph, cloud discovery candidates/run state, experience/research records, and sandbox practical assessment.
- A stale/reclaimed owner is rejected before the domain mutation. Existing queue completion, pause/cancel, retry and checkpoint semantics remain owned by the #799 task manager/TaskContext implementation.

The exact source-file map and state-transition table are in docs/DOMAIN_TASK_WRITE_FENCING_AUDIT_2026-11-22.fa.md. This addendum remains proposed until exact-head CI passes and the target-host Windows/Runner and crash/restart gates are accepted. It does not claim that all domain writes across the repository have been audited.
