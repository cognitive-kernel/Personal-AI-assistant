# ADR-008: Runtime recovery for orphaned AI-learning runs

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/ai_learning/browser_automation.py`, `app/main.py`, learning-runtime maintenance

## Context

The AI-learning browser worker persists runs in SQLite and recovers interrupted runs during application startup. If a worker thread disappears unexpectedly while the application process remains alive, the run can remain marked `running` because startup recovery is not invoked again.

## Decision

- Invoke `recover_orphaned_runs()` from the existing learning-runtime maintenance callback, which runs on the configured 30-second maintenance interval.
- Reconcile persisted `running` runs only after acquiring the durable process-wide SQLite browser-worker lease. A process-local `_ACTIVE` flag is insufficient because another application process may own the browser.
- The lease uses a process-unique owner, monotonically increasing epoch, 90-second expiry and 15-second heartbeat. Startup recovery and the watchdog must not requeue a run while another owner holds an unexpired lease.
- Global pause, per-topic pause, cancellation and destructive reset must coordinate with the remote worker through persisted state; a worker that loses ownership must discard its late provider result.
- Do not create new runs or lessons as part of recovery. Preserve stored partial results and let the existing bounded browser-capture retry policy handle transient failures.
- Respect global learning pause, explicit cancellation, shutdown, and `needs_user_action`. The watchdog must not reset shutdown state or revive terminal `failed`/`cancelled` runs.
- Log the recovery outcome and requeued count; retry on the next maintenance interval if recovery raises.

## Consequences and limitations

After the last heartbeat stops, a crashed process becomes reclaimable when its 90-second lease expires; the next 30-second maintenance interval then detects and recovers it (normally within 90–120 seconds). A browser call that is still alive but hung remains subject to the existing response timeout and retry policy; this watchdog does not forcibly terminate it. The lease coordinates application processes on one host that share the same local AI-learning SQLite database and persistent Chrome profile. It is not a multi-host distributed queue, does not support a network filesystem, and cannot restore the exact ChatGPT tab/session after a process crash. The heartbeat and fencing behavior must remain covered by automated concurrency and expiry tests. Target-host restart/soak, long-running-call recovery, and actual browser-profile behavior remain separate operational acceptance items.
