# ADR-014: Atomic readiness and ownership for autonomous checkpoints

- **Status:** Proposed — pending exact-head CI and target-runner verification
- **Date:** 2026-11-05
- **Scope:** Autonomous engineering checkpoint startup and recovery

## Context

A Windows autonomous run wrote the run-scoped checkpoint metadata, including its base SHA and watcher PID, but the controller did not observe the separate `.ready` marker and timed out. The startup protocol treated two independently replaced files as one readiness transaction. A partially published pair could therefore leave the controller unable to distinguish a completed initial snapshot from an incomplete startup.

The existing execution controller already serializes worktree-mutating cycles with `repository_execution_lock`. Checkpoint files are namespaced by workflow run and task. The watcher must remain concurrent with the coding process because its purpose is to snapshot the active worktree; taking the repository execution lock inside the watcher would deadlock the intended workflow.

## Decision

1. The watcher writes the patch and optional plan first, then atomically replaces one metadata document. That replacement is the readiness commit point.
2. The metadata contract is versioned and binds readiness to the exact `run_id`, `task`, immutable `base_sha`, and watcher PID. The controller accepts readiness only when all fields match and `watcher_ready` is explicitly true.
3. The separate `.ready` file is no longer written. Stale markers are removed during startup/cleanup for compatibility, but no synchronous secondary filesystem operation follows the readiness commit point.
4. The `.error` marker remains the explicit startup-failure channel. The controller still fails closed if the watcher exits, reports an error, or does not publish matching readiness metadata before the bounded startup deadline.
5. The controller retains exclusive ownership of the repository worktree through `repository_execution_lock`. The watcher owns only its run/task-scoped checkpoint files and does not mutate Git history or switch branches.
6. Recovery remains controller-owned: while holding the execution lock, the controller identifies the latest checkpoint, stops its recorded watcher only after validating its process command line, verifies the checked-out HEAD against the immutable base SHA, and applies the saved patch only after `git apply --check` succeeds. Failed restores preserve the checkpoint.

## Compatibility

- The checkpoint metadata schema advances to version 3 for new watcher readiness records.
- Existing version 2 checkpoint records remain readable by the recovery path; restore compatibility is not based on the readiness schema.
- Existing per-run/task file names and the command-line interface remain unchanged.

## Verification requirements

- Unit tests for the versioned metadata contract and owner/base/PID mismatch rejection.
- Regression test proving readiness succeeds when the optional marker cannot be written.
- Real child-process readiness integration test.
- Cross-process execution-lock exclusion and run/task checkpoint-isolation tests.
- Windows storage regression workflow and the full required CI suite on the exact PR head.
- The first subsequent target-runner cycle must confirm that the original startup timeout is gone and that checkpoint restore/cleanup still work. This operational confirmation is separate from repository CI.

## Consequences

The startup decision no longer depends on two independent filesystem writes. The repository execution lock remains the single controller-level worktree ownership boundary, while checkpoint metadata provides run/task-level watcher ownership and recovery provenance. The readiness marker is retired from the write path so it cannot block a watcher after its durable snapshot has committed.
