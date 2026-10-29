# ADR-003: Automatic restart of interrupted browser-learning courses

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/browser_learning.py`, startup in `app/main.py`, browser-learning course routes

## Context

The legacy browser-learning API persisted course and lesson state, but startup only marked queued/running courses as `interrupted`. It did not schedule them again, so a process restart could leave an unfinished course idle until the user manually retried it.

## Decision

- At startup, mark only stale running courses as interrupted; preserve courses that were already queued, and reset only unfinished `running`/`failed` lesson rows to queued.
- Dispatch the oldest persisted queued/interrupted course through a single process-local worker slot.
- Route new course and manual retry requests through the same dispatcher instead of invoking the worker directly.
- After a course succeeds, dispatch the next persisted queued/interrupted course. Retryable runtime failures and process interruptions are persisted as `interrupted` with a durable `next_retry_at`; unfinished lesson rows are reset to `queued` while completed lesson content remains unchanged. The learning runtime checks stale running rows and invokes the FIFO dispatcher every 30 seconds. The dispatcher honors retry deadlines and stops behind an earlier terminally failed course. Recovery only proceeds after acquiring the durable worker lease, so another live process is not rewritten as stale. Automatic retries use bounded delays of 30, 120, and 300 seconds, then leave the course `failed` for manual recovery. Login/session gates and missing Playwright are non-retryable because retrying cannot resolve them.
- Keep completed lesson content intact; `run_course` skips completed lessons and continues unfinished lessons from the stored outline.
- Automatic retry is bounded to three scheduled retries. A course that exhausts the budget or needs user/environment action remains `failed` until an explicit retry; no unbounded retry loop is allowed.

## Consequences and limitations

This provides automatic recovery after process restart, bounded retry after transient runtime failures, and FIFO draining of already-persisted courses. Retry count and deadline are durable schema fields introduced by migration 41; existing course rows are preserved with zero retries. Cross-process ownership is specified separately in [ADR-004](ADR-004-browser-learning-worker-lease.md). Persistent failures and conditions requiring user action remain failed for deliberate recovery. Target-host behavior must be validated separately.
