# ADR-003: Managed task runtime lifecycle

- Status: Accepted
- Date: 2026-10-15

## Decision

TaskRuntime owns polling worker threads and provides explicit start/stop operations. It is independent of FastAPI so lifecycle behavior can be tested without importing the web application. Worker count, polling interval, and lease duration are validated at construction.

The runtime does not invent handlers or enable itself at import time. `app/main.py` supplies a reviewed allow-listed registry for learning, research, cloud discovery, and only environment-authorized sandbox/experience handlers; startup reconciles queued learning jobs before starting the runtime, and shutdown stops the runtime with a bounded deadline. Startup fails closed when the storage schema is unrecognized. The runtime remains independent of FastAPI for unit testing.

## Shutdown semantics

Shutdown sets a shared stop event and joins workers against a single bounded deadline. Idle workers stop promptly. A handler already executing is cooperative and may outlive the requested timeout; the runtime reports this rather than claiming it stopped successfully.

## Heartbeat failure policy

The worker retries transient SQLite operational errors during lease renewal only until the last known lease deadline, measured with a monotonic clock. A successful renewal resets that deadline. Ownership loss, pause/cancel transitions, or unexpected exceptions stop heartbeat renewal; the worker must not treat a failed renewal as proof of continued ownership. The regression test `test_heartbeat_retries_transient_sqlite_operational_error` verifies recovery after one transient database-lock error.

## Acceptance gates

- Tests cover task processing, idempotent start, idle shutdown, and invalid configuration.
- CI must pass on the exact pull-request head.
- [x] Application startup/shutdown integration and a reviewed handler registry are implemented in `app/main.py`.
- [x] Startup reconciliation and idempotent learning-task enqueue are covered by `tests/test_main_startup.py`.
- [x] CI on main commit `65b959be2394901bdcbafe94ae4bfb85a388f158` passes: [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39271785980), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39271786146), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39271786065).
- [ ] Target-host crash/restart, sustained-load, and full task-type fault-injection acceptance remains outstanding.
