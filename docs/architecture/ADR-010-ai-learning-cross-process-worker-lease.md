# ADR-010: Cross-process ownership for the AI-learning browser worker

- Status: Proposed pending exact-head CI and code review
- Date: 2026-10-30
- Scope: `app/ai_learning/db.py`, `app/ai_learning/service.py`, `app/ai_learning/browser_automation.py`

## Context

The AI-learning queue stores run state in SQLite, but its previous concurrency guard (`_ACTIVE` and `_LOCK`) existed only inside one Python process. Startup recovery and the 30-second watchdog could therefore mistake another process's live `running` row for an orphan. Two processes could also select the same queued run because `start_run` accepted an already-running row. The dedicated persistent Chrome profile is a shared external resource and must have one owner.

## Decision

- Add checksummed migration 7 with `worker_lease_owner`, `worker_lease_until`, and monotonically increasing `worker_lease_epoch` columns on the singleton `ai_learning_control` row.
- Acquire/renew/release the lease in SQLite transactions. A lease is valid only while its expiry is in the future; a new owner increments the epoch. The lease defaults to 90 seconds and the browser worker renews it every 15 seconds.
- Require queue dispatch, startup recovery, orphan recovery, manual resume, and outline starts to hold the process-wide lease before claiming or requeueing work.
- Preserve one browser worker per AI-learning database/profile. Requests from another process remain in the durable queue rather than launching a second Chrome session.
- On heartbeat loss, pause, cancellation, or topic pause, signal the worker cooperatively. Before persisting captured output, check the current lease generation; a stale worker must discard its result.
- Refuse destructive learning-data reset while any worker owner remains recorded, including an expired lease not yet reclaimed/released; expiry alone does not prove the old browser thread has stopped. Global pause requeues active work in durable storage so another process cannot mistake the transition for a successful completion.
- Keep the lease scoped to a single host using one local SQLite database and one dedicated browser profile. This is not a multi-host distributed queue.

## Consequences and limitations

- A process crash stops its heartbeat; another process may recover the run only after the 90-second lease expires. The prompt may need to be submitted again because exact ChatGPT tab/session continuation is not persisted.
- A heartbeat and fencing epoch reduce duplicate execution and stale database writes, but they cannot forcibly terminate an already-hung browser call or reverse a prompt already sent to ChatGPT.
- SQLite on a network filesystem and separate per-host database copies are outside this design. Do not run multiple hosts against separate stores and expect coordination.
- Automated tests must cover owner exclusion, expiry/reclaim, stale-owner fencing, startup/watchdog behavior, reset safety, pause/cancel propagation, and existing queue/retry regressions. Target-host restart/soak and actual Chrome profile behavior remain separate operational acceptance gates.
