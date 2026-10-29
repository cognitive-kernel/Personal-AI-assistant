# ADR-003: Automatic restart of interrupted browser-learning courses

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/browser_learning.py`, startup in `app/main.py`, browser-learning course routes

## Context

The legacy browser-learning API persisted course and lesson state, but startup only marked queued/running courses as `interrupted`. It did not schedule them again, so a process restart could leave an unfinished course idle until the user manually retried it.

## Decision

- At startup, mark stale queued/running courses as interrupted and reset only lesson rows that were still running to queued.
- Dispatch the oldest persisted queued/interrupted course through a single process-local worker slot.
- Route new course and manual retry requests through the same dispatcher instead of invoking the worker directly.
- After a course succeeds, dispatch the next persisted queued/interrupted course. If a course fails, stop draining the queue so one systemic provider/authentication/dependency failure does not cascade across all pending courses.
- Keep completed lesson content intact; `run_course` skips completed lessons and continues unfinished lessons from the stored outline.
- A course that fails during a run is terminal until an explicit retry; this prevents a permanent provider/authentication failure from causing an infinite retry loop.

## Consequences and limitations

This provides automatic recovery after process restart and FIFO draining of already-persisted courses in a single application process. It does not provide cross-process leases, so this route must not be scaled to multiple web workers without a separate lease/heartbeat design. Runtime provider failures still surface as failed courses for deliberate retry. Target-host behavior must be validated separately.
