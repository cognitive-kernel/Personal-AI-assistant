# ADR-007: Cross-process lease for browser-learning workers

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/browser_learning.py`, learning-store migration 40

## Context

A process-local lock prevents duplicate browser-learning workers only inside one Python process. Without durable ownership, a second web process could mark a live course as interrupted and start a second Chrome/ChatGPT interaction against the same persisted course.

## Decision

- Add the `browser_learning_worker_lease` singleton table through registered learning-store migration 40.
- Claim and renew the lease in `BEGIN IMMEDIATE` transactions; another owner cannot acquire a lease whose expiry is still in the future.
- Use a process-unique owner token (including a PID-specific identity regenerated after fork) and a 240-second lease, renewed every 15 seconds by a heartbeat thread.
- Startup recovery must acquire the lease before changing any queued/running course state. If another process owns it, recovery does not mutate that process's live course.
- If heartbeat renewal fails, mark the local worker's lease as lost and stop persisting new generated content. Do not delete a lease owned by another process; takeover is allowed after expiry.
- Release the lease after startup recovery commits its state updates, a successful queue drain, a terminal failure, or a controlled worker-start failure. Startup recovery does not retain ownership while idle. Startup recovery also attempts release in a `finally` block if its recovery transaction raises, so a rolled-back recovery does not strand the worker slot until lease expiry.

## Consequences and limitations

This prevents normal concurrent processes from claiming the same worker slot while the lease remains valid. After a hard process crash, recovery may wait up to the lease duration (240 seconds). A long external browser interaction cannot be forcibly cancelled instantly on lease loss; the worker checks ownership at persistence boundaries and must not save a response after detecting lease loss. This mechanism is not a substitute for target-host concurrency/soak testing, and external ChatGPT requests already sent before lease loss cannot be undone.
