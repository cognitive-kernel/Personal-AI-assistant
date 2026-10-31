# ADR-014: Register task runtimes before starting worker threads

- Status: Accepted
- Date: 2026-10-31
- Scope: FastAPI startup and managed task-runtime lifecycle

## Context

The application startup path constructed each `TaskRuntime`, called `start()`, and only then appended it to the module-level runtime registry. If `start()` raised after partially starting threads, the runtime was not present in the registry used by FastAPI lifespan shutdown. `TaskRuntime.start()` already requests stop and performs a bounded join on partial startup, but a non-cooperative thread can outlive that first cleanup deadline. An unregistered runtime would then be invisible to the application-level shutdown pass.

The same ordering existed for the fallback/dead-letter runtime.

## Decision

- Add each newly constructed runtime to `_task_runtimes` before calling `start()`.
- Apply the same order to normal lane runtimes and the fallback runtime.
- Keep the existing `TaskRuntime.start()` rollback and bounded join behavior unchanged.
- Rely on the FastAPI lifespan `finally` shutdown path to stop every registered runtime even when startup raises before the application begins serving.
- Preserve the registry after shutdown so a later startup can inspect and stop any surviving threads before rebuilding the runtime set.

## Verification

A startup regression injects a failure from the first runtime's `start()`. It verifies that the runtime is already registered when the exception propagates and that the shutdown path calls `stop()` on it. Existing startup idempotence, partial-health recovery, and runtime thread-recovery tests remain in place.

## Consequences and limits

- A partially started runtime remains visible to application shutdown and subsequent startup recovery.
- No queue schema, worker identity, task payload, or handler contract changes.
- This does not guarantee that a non-cooperative handler will stop; it ensures the runtime remains tracked and shutdown/restart can report the failure rather than silently orphaning it.
