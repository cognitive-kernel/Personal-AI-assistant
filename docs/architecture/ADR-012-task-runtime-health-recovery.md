# ADR-012: Detect and recover partial task-runtime thread failure

- Status: Accepted
- Date: 2026-10-31
- Scope: in-process task worker and maintenance thread lifecycle

## Context

A task runtime owns a configured set of worker threads and, optionally, one maintenance thread. The previous `is_running` property returned true when any managed thread was alive. That is useful for shutdown accounting, but it is not sufficient to establish that the configured runtime is healthy. Startup treated `is_running` as a health check, and `start()` treated any live thread as proof that the runtime was already started. If one worker or the maintenance thread exited unexpectedly, surviving threads could keep the runtime from being rebuilt, silently reducing worker capacity or disabling maintenance.

Starting replacement threads before stopping surviving threads is also unsafe: the old and new workers may share the same worker identity and compete for leases.

## Decision

- Preserve `is_running` as an activity/shutdown property: it is true while any managed thread remains alive.
- Add `is_healthy` as a separate readiness property. It is true only when every configured worker thread is alive and, when configured, the maintenance thread is alive.
- Make `start()` idempotent only for a fully healthy runtime.
- When a runtime is partially alive, request stop and join every surviving managed thread against one bounded 10-second deadline before creating replacements.
- If any old thread remains alive at the deadline, fail closed with `RuntimeError`; do not create overlapping replacement workers.
- Make application startup use `is_healthy` to decide whether the complete runtime set can be reused. Continue using `is_running` to decide whether a runtime needs shutdown.

## Verification

Regression tests simulate unexpected worker-thread and maintenance-thread termination with `SystemExit`. They verify that the runtime reports unhealthy while surviving threads remain active, stops the old thread set before restarting, restores all configured threads, and shuts down cleanly. The startup regression verifies that a partially alive but unhealthy runtime set is rebuilt.

## Consequences and limits

- The runtime can recover from an exited worker or maintenance thread when surviving threads stop cooperatively.
- A non-cooperative handler that remains alive beyond the restart deadline blocks replacement and surfaces an error instead of allowing duplicate workers.
- This is single-process thread lifecycle recovery; it does not provide cross-process orchestration or host-level supervision.
