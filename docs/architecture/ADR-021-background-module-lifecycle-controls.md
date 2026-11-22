# ADR-021: Durable lifecycle controls for background task modules

- Status: Proposed; implementation and exact-head CI pending
- Date: 2026-11-22
- Scope: feature-managed modules executed by the shared durable TaskRuntime

## Context

The dashboard already exposes per-task pause/resume/cancel operations. That is insufficient when an operator wants to disable one background module (for example, cloud resource discovery) without stopping unrelated modules that share the TaskRuntime process.

A module-level control must survive process restart, be race-safe against task claims, preserve checkpoints, and avoid terminating shared Python worker threads or corrupting other modules.

## Decision

The explicit registry in `app/module_control.py` maps every feature-managed task type to exactly one independently controllable module. The registry coverage test compares it with `FEATURE_MANAGED_TASK_TYPES`; a newly introduced feature task type must be assigned to a module before this test can pass.

Module state is persisted in the additive `module_controls` table. The default for a module without a row is `running`, preserving existing behavior after migration.

| Action | Module state | Queue behavior |
|---|---|---|
| Start | running | New tasks may be claimed; paused tasks remain paused |
| Pause | paused | Queued tasks become paused; active tasks receive pause_requested and stop at a safe checkpoint |
| Resume | running | Paused tasks return to queued; pending pause requests are cleared |
| Finish (drain) | draining | No new task is claimed; active tasks may finish; queued tasks remain intact |
| Force stop | stopped | Queued tasks remain paused for later resume; active tasks receive cancel_requested |

Task claim filtering reads module state inside the same `BEGIN IMMEDIATE` transaction as the claim. A concurrent pause, drain, or stop therefore cannot race between a separate admission check and the queue claim.

All task status transitions pass through the existing `task_queue` table and its audit triggers. Checkpoint JSON, result JSON, task payloads, lease epochs, and unrelated module rows are not rewritten by module control actions.

## Force-stop boundary

Python worker threads are not asynchronously killed. That is unsafe for shared SQLite connections, locks, subprocess ownership, and in-flight domain transactions. Force stop preserves waiting work as paused and signals active work through the existing cooperative cancellation protocol; the active run becomes cancelled only when its worker observes the request at a checkpoint or heartbeat. An active handler stops at its next checkpoint or heartbeat; an uncooperative handler remains visibly pending rather than being falsely reported as stopped.

The UI must describe this boundary and show active/cancel-requested counts.

## Security

The module-control endpoints inherit the existing loopback-only task API dependency. No remote control endpoint is introduced. Task payloads and private result bodies are never returned by the module status API.

## Independent lifecycle adapters

The dashboard also lists services with separate execution owners, but each action delegates to that owner rather than writing to the shared TaskRuntime queue:

- AI-learning Playwright worker: durable `draining` state is added through AI-learning migration 8. Finish blocks new dispatch while allowing the current run and its lease to complete. Pause cooperatively requeues active work. Force stop cancels active run records, keeps queued run records, and waits for the worker to observe cancellation. Start/Resume clears pause/drain and uses the existing durable dispatcher.
- Opt-in backup scheduler: status and actions delegate to `BackupScheduler`. Pause/Finish/Force Stop signal the scheduler to stop after any in-progress snapshot; a SQLite snapshot is never killed mid-write. Force Stop reports `stopping` if the bounded wait expires. Start/Resume remain disabled until `DATABASE_BACKUP_INTERVAL_HOURS` is configured.
- Ollama model pull: the model download has a per-job cooperative cancel endpoint. It closes only that job's streaming response and never removes an installed model. If the connection is still being established, the status remains `cancelling` until the network operation unwinds.
- Autonomous Engineering: its dedicated UI already owns Start/Dispatch, Pause, Resume, and Force Stop/Cancel through the GitHub Actions API. A separate Finish control is exposed as the same safe boundary operation as Pause; neither action kills a runner process or deletes its checkpoint.

Synchronous chat requests and other request/response operations are not background modules and are not given fake lifecycle controls. The dashboard reports unavailable/disabled external owners explicitly instead of claiming that they are running.

## Acceptance

- Migration 49 for TaskRuntime module controls and AI-learning migration 8 are additive; existing task/run/checkpoint/result rows remain intact.
- Every feature-managed task type belongs to exactly one registry module.
- Pausing/stopping one TaskRuntime module cannot block claims for another.
- Finish drains active work without deleting or cancelling queued work.
- Force stop preserves checkpoints and uses cooperative cancellation; it never asynchronously kills shared Python threads.
- AI-learning drain/force-stop, backup scheduler stop-after-snapshot, and Ollama pull cancellation have owner-specific regression tests.
- Loopback API, unit tests, browser E2E, Windows CI, exact-head CodeQL, and dependency checks pass.
- Target-host acceptance remains a separate gate; CI alone is not target-host verification.
