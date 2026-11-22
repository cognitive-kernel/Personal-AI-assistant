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
| Force stop | stopped | Queued/paused tasks become cancelled; active tasks receive cancel_requested |

Task claim filtering reads module state inside the same `BEGIN IMMEDIATE` transaction as the claim. A concurrent pause, drain, or stop therefore cannot race between a separate admission check and the queue claim.

All task status transitions pass through the existing `task_queue` table and its audit triggers. Checkpoint JSON, result JSON, task payloads, lease epochs, and unrelated module rows are not rewritten by module control actions.

## Force-stop boundary

Python worker threads are not asynchronously killed. That is unsafe for shared SQLite connections, locks, subprocess ownership, and in-flight domain transactions. Force stop cancels waiting work immediately and signals active work through the existing cooperative cancellation protocol. An active handler stops at its next checkpoint or heartbeat; an uncooperative handler remains visibly pending rather than being falsely reported as stopped.

The UI must describe this boundary and show active/cancel-requested counts.

## Security

The module-control endpoints inherit the existing loopback-only task API dependency. No remote control endpoint is introduced. Task payloads and private result bodies are never returned by the module status API.

## External lifecycle adapter: Autonomous Engineering

Autonomous Engineering is not a TaskRuntime lane. Its lifecycle is owned by GitHub Actions and is controlled through a separate adapter exposed in the same dashboard:

| Action | Durable state | Behavior |
|---|---|---|
| Start | enabled=true | Enable future cycles and dispatch one bounded workflow; an existing pause is not silently cleared |
| Pause | paused=true | Prevent the next task from starting; an already-running bounded task may finish |
| Resume | enabled=true, paused=false | Clear pause and request continuation from durable state |
| Finish | enabled=false, paused=false | Disable future cycles while allowing the current workflow run to settle |
| Force stop | enabled=false, paused=true | Disable future cycles and request cancellation of the active GitHub Actions run |

The workflow checks the enabled flag at its trigger gate and re-reads the live enabled/pause variables immediately before task execution. A missing enabled variable defaults to true for backward compatibility. Force Stop is a GitHub cancellation request; it is not evidence that the Windows Runner process tree has exited. Runner process ownership, checkpoint recovery, and restart behavior still require target-host acceptance.

## Out of scope / separate runtimes

The TaskRuntime registry covers feature-managed task types only. Synchronous chat requests, AI-learning's independent Playwright worker, Browser Learning's course worker, and the opt-in backup scheduler have separate lifecycle owners and must be integrated through their own safe adapters; they must not be represented as controlled by the TaskRuntime registry until those adapters exist. Autonomous Engineering has its separate adapter described above.

## Acceptance

- Migration is additive and leaves existing queue rows unchanged.
- Every feature-managed task type belongs to exactly one registry module.
- Pausing/stopping one module cannot block claims for another.
- Finish drains active work without deleting or cancelling queued work.
- Force stop preserves checkpoints and uses cooperative cancellation.
- Loopback API, unit tests, browser E2E, Windows CI, and exact-head CodeQL/dependency checks pass.
- Target-host acceptance remains a separate gate; CI alone is not target-host verification.
