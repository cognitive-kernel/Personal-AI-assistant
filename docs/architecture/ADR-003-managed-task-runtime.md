# ADR-003: Managed task runtime lifecycle

- Status: Proposed
- Date: 2026-10-15

## Decision

TaskRuntime owns polling worker threads and provides explicit start/stop operations. It is independent of FastAPI so lifecycle behavior can be tested without importing the web application. Worker count, polling interval, and lease duration are validated at construction.

The runtime does not invent handlers or enable itself at import time. The application must supply a reviewed handler registry and decide when to start/stop the runtime. This increment does not yet register learning handlers or start the runtime in FastAPI.

## Shutdown semantics

Shutdown sets a shared stop event and joins workers against a single bounded deadline. Idle workers stop promptly. A handler already executing is cooperative and may outlive the requested timeout; the runtime reports this rather than claiming it stopped successfully.

## Acceptance gates

- Tests cover task processing, idempotent start, idle shutdown, and invalid configuration.
- CI must pass on the exact pull-request head.
- Application startup/shutdown integration must only happen after a safe handler registry is implemented.
