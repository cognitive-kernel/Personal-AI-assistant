# ADR-008: Runtime recovery for orphaned AI-learning runs

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/ai_learning/browser_automation.py`, `app/main.py`, learning-runtime maintenance

## Context

The AI-learning browser worker persists runs in SQLite and recovers interrupted runs during application startup. If a worker thread disappears unexpectedly while the application process remains alive, the run can remain marked `running` because startup recovery is not invoked again.

## Decision

- Invoke `recover_orphaned_runs()` from the existing learning-runtime maintenance callback, which runs on the configured 30-second maintenance interval.
- Reconcile persisted `running` runs only when no worker is active in this process; requeue them using the existing service transaction and dispatch the oldest durable queued run.
- Do not create new runs or lessons as part of recovery. Preserve stored partial results and let the existing bounded browser-capture retry policy handle transient failures.
- Respect global learning pause, explicit cancellation, shutdown, and `needs_user_action`. The watchdog must not reset shutdown state or revive terminal `failed`/`cancelled` runs.
- Log the recovery outcome and requeued count; retry on the next maintenance interval if recovery raises.

## Consequences and limitations

Recovery after an orphaned thread is detected on the next maintenance interval, normally within 30 seconds. A browser call that is still alive but hung remains subject to the existing response timeout and retry policy; this watchdog does not forcibly terminate it. The AI-learning browser automation route still assumes a single application process and does not have a cross-process worker lease. Multi-worker deployment remains unsupported until a durable lease is implemented and validated. Target-host restart, long-running call, and concurrency behavior remain operational acceptance items.
