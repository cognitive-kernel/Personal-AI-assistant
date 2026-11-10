# ADR-014: Atomic readiness and ownership for autonomous checkpoints

- **Status:** Proposed — pending exact-head CI and target-runner verification
- **Date:** 2026-11-05
- **Scope:** Autonomous engineering checkpoint startup and recovery

## Context

A Windows autonomous run wrote the run-scoped checkpoint metadata, including its base SHA and watcher PID, but the controller did not observe the separate `.ready` marker and timed out. The startup protocol treated two independently replaced files as one readiness transaction. A partially published pair could therefore leave the controller unable to distinguish a completed initial snapshot from an incomplete startup.

A subsequent recovery audit found a more serious ownership defect: `stop_existing_watcher()` silently ignored `psutil.AccessDenied`, then restore/clear continued as if the watcher had stopped. PID and command-line matching also did not distinguish a reused PID from the original process. That allowed a live process to keep replacing checkpoint files while recovery or cleanup was modifying them.

The existing execution controller already serializes worktree-mutating cycles with `repository_execution_lock`. Checkpoint files are namespaced by workflow run and task. The watcher must remain concurrent with the coding process because its purpose is to snapshot the active worktree; taking the repository execution lock inside the watcher would deadlock the intended workflow.

## Decision

1. The watcher writes the patch and optional plan first, then atomically replaces one metadata document. That replacement is the readiness commit point.
2. The metadata contract is versioned and binds readiness to the exact `run_id`, `task`, immutable `base_sha`, watcher PID, and OS process creation time. The controller accepts readiness only when all fields match and `watcher_ready` is explicitly true.
3. Watcher lifecycle is isolated in `autonomous_engineering_checkpoint_process.py`; checkpoint persistence does not own process-inspection policy. A PID with a different creation time is treated as reused and is never terminated. A live process whose identity cannot be inspected or whose command line does not match the recorded run/task/base blocks restore and cleanup.
4. The separate `.ready` file is no longer written. Stale markers are removed during startup/cleanup for compatibility, but no synchronous secondary filesystem operation follows the readiness commit point.
5. The `.error` marker remains the explicit startup-failure channel. The controller still fails closed if the watcher exits, reports an error, or does not publish matching readiness metadata before the bounded startup deadline.
6. The controller retains exclusive ownership of the repository worktree through `repository_execution_lock`. The watcher owns only its run/task-scoped checkpoint files and does not mutate Git history or switch branches. For Git diff discovery, it creates a temporary index seeded from the immutable base SHA and removes it after each snapshot; it never runs `git add -N` against the shared `.git/index` while Codex is active.
7. Recovery remains controller-owned: while holding the execution lock, the controller stops and reaps the exact recorded watcher before touching checkpoint files, verifies the checked-out HEAD against the immutable base SHA, and applies the saved patch only after `git apply --check` succeeds. Any ambiguous process identity, access denial, timeout, or failed restore preserves the checkpoint and aborts recovery.

## Compatibility

- The checkpoint metadata schema advances to version 4. Readiness requires a positive watcher PID and finite OS process creation time.
- Existing version 2/3 checkpoint records remain readable as checkpoint data. If an old record points to a live process whose ownership cannot be proven, recovery fails closed and preserves the files; it never guesses or kills an unrelated process.
- Existing per-run/task file names and the command-line interface remain unchanged.

## Verification requirements

- Unit tests for the versioned metadata contract and owner/base/PID/process-start mismatch rejection.
- Fault-injection tests for access denied, reused PID, command-line mismatch, legacy live watcher, process termination timeout/kill, and missing process.
- Regression test proving readiness is committed without a secondary marker file.
- Real child-process readiness integration test.
- Cross-process execution-lock exclusion, run/task checkpoint-isolation, and shared-index immutability tests.
- Windows storage regression workflow and the full required CI suite on the exact PR head.
- The first subsequent target-runner cycle must confirm that the original startup timeout is gone and that checkpoint restore/cleanup still work. This operational confirmation is separate from repository CI.

## Consequences

The startup decision no longer depends on two independent filesystem writes. The repository execution lock remains the single controller-level worktree ownership boundary, while checkpoint metadata provides run/task-level watcher ownership and recovery provenance. The readiness marker is retired from the write path so it cannot block a watcher after its durable snapshot has committed. Snapshot discovery uses a private temporary Git index, eliminating interference with Codex or controller staging operations.
