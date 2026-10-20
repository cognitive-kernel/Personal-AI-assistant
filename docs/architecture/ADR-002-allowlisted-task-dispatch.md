# ADR-002: Allow-listed task dispatch

- Status: Accepted
- Date: 2026-10-15

## Decision

The worker executes only application-registered Python handlers keyed by a task_type. A task payload is data, not executable code. Unknown task types fail without attempting shell execution, dynamic imports, or network access.

The dispatcher exposes run_once() for deterministic tests and is hosted by the managed TaskRuntime, which owns bounded polling threads and shutdown. app/main.py supplies an explicit allow-listed registry for reviewed task types; unknown types fail closed. The runtime starts only after storage initialization/schema validation and learning-job reconciliation.

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
- The managed polling loop, bounded shutdown, worker-count/poll interval validation, and learning handler integration are implemented. Target-host load/soak evidence and broader fault-injection coverage remain release gates. Arbitrary payload execution remains prohibited; isolated sandbox work is available only through reviewed handlers and explicit environment authorization.
