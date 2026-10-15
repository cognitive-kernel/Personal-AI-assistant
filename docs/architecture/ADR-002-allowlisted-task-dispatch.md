# ADR-002: Allow-listed task dispatch

- Status: Proposed
- Date: 2026-10-15

## Decision

The worker executes only application-registered Python handlers keyed by a task_type. A task payload is data, not executable code. Unknown task types fail without attempting shell execution, dynamic imports, or network access.

The first dispatcher exposes run_once() rather than starting an implicit background loop at application import/startup. This keeps worker lifecycle, concurrency limits, shutdown behavior, and resource accounting explicit until an application-owned handler and runtime configuration are available.

## Handler contract

A handler receives:
- the persisted payload object;
- the last persisted checkpoint, if any;
- a TaskContext with checkpoint, control-status, and lease-heartbeat operations.

Handlers must return a JSON object. Exceptions are recorded as bounded task errors and retried only while the configured attempt budget permits. The worker checks cooperative pause/cancel requests before marking a result successful. If ownership/lease is lost, it does not attempt a stale failure or completion write.

## Safety boundaries

This dispatcher is not a sandbox and must not register a handler that executes arbitrary task payloads as code or shell commands. Future laboratory tasks require a separately constrained execution backend and a narrow, reviewed handler that submits work to that backend.

## Acceptance gates

- Tests cover allow-list enforcement, success, retry/exhaustion, invalid result shape, pause/cancel at checkpoints, and empty/invalid handler registries.
- CI must pass on the exact pull request head.
- A production polling loop, shutdown integration, CPU/RAM concurrency controls, and concrete learning handlers are follow-up work.
