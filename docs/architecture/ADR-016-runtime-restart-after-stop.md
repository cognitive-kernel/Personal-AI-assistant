# ADR-016: Treat a pending task-runtime shutdown as unhealthy

- Status: Accepted
- Date: 2026-10-31
- Scope: managed task-runtime start/stop lifecycle

## Context

`TaskRuntime.stop()` sets the stop event before joining worker threads. A cooperative task handler may still be unwinding after a bounded `stop(timeout=...)` returns `False`. During that interval, every configured thread can still be alive even though the runtime has already been asked to shut down.

Previously, `is_healthy` checked only thread liveness. A concurrent or immediate `start()` could therefore treat the stopping runtime as healthy and return idempotently. When the active handler later returned, all workers exited and the caller was left with no running workers despite having requested a start.

## Decision

- A runtime with its stop event set is not healthy, even while managed threads remain alive.
- `start()` continues to serialize lifecycle changes with the existing lock.
- If shutdown is pending, `start()` follows the existing partial-runtime path: it joins previous threads against the bounded deadline and only creates replacements after all prior threads have stopped.
- If an old handler does not stop within the existing deadline, restart remains fail-closed; no overlapping worker with the same identity is created.

## Verification

A regression starts a blocking cooperative handler, requests `stop(timeout=0)`, verifies the timed-out runtime is not healthy, releases and joins the old worker, then starts a fresh worker and confirms the runtime is healthy again.

## Consequences and limits

- A start request cannot be silently discarded because a shutdown is still in progress.
- No task schema, lease, queue state, worker identity, handler contract, or retry policy changes.
- This does not forcibly terminate non-cooperative handlers; bounded shutdown and fail-closed restart behavior remain unchanged.
