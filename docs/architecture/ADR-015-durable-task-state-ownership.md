# ADR-015: Cross-process ownership of durable autonomous task state

- **Status:** Proposed, pending exact-head CI and Windows Runner acceptance
- **Date:** 2026-11-10
- **Scope:** Manual task persistence, pending PR ownership, recovery, and task completion

## Context

The autonomous workflow has one durable manual-task slot and one pending autonomous-PR record. Those files are accessed by different execution paths:

- The PowerShell persistence step runs inside the `codex` job, after it enters the repository-wide GitHub Actions concurrency group.
- The workflow's Python capture step reads the task under the same local durable-state mutex.
- The Python cycle controller loads, saves, and clears pending PR state.
- PR publication occurs in a child process while the controller retains repository worktree ownership.
- After owner merge and exact post-merge CI, the controller clears the task and pending PR.

Atomic file replacement protects an individual write from partial contents, but it does not serialize a read/validate/write transaction across PowerShell and Python. Previously, the PowerShell persistence job ran before the `codex` concurrency group, so two dispatches could race while checking and writing the single task slot. Persistence now runs only after the serialized `codex` job has acquired its concurrency slot. A second dispatch received while the workflow is paused or another run is active fails visibly before it can be accepted; it is not silently queued or written into the active run's slot.

## Decision

1. Introduce `scripts/autonomous_engineering_state_lock.py` as the shared durable-state synchronization contract.
2. On Windows, Python and PowerShell acquire the same named kernel mutex:
   `Local\PersonalAIAssistant.AutonomousEngineering.State.v1`.
   The mutex is process/thread-owned by the OS and is abandoned automatically on process death. Both runtimes treat an abandoned mutex as acquired, then validate the persisted state before proceeding.
3. On POSIX, the same Python API uses a non-blocking `flock` loop with a bounded timeout. The lock file is diagnostic/persistent; the kernel lock, not the file's presence, determines ownership.
4. Resolve the state root through `autonomous_engineering_state_paths.py` for checkpoint, execution, progress telemetry, and task/PR state. Expand environment variables consistently across Python and PowerShell.
5. PowerShell manual-task persistence runs inside the `codex` job's GitHub concurrency group and holds the shared mutex across active-run detection, pending-task/pending-PR validation, open-PR checks, and atomic task write. It rejects a new single-slot task while another autonomous run is queued or active; the schedule gate fails the manual dispatch visibly when paused or occupied. It never silently queues a task that a currently running cycle could later erase.
6. The workflow capture step is read-only for the durable task file. It acquires the shared mutex, verifies any supplied task exactly matches the task already persisted by the earlier step in the same serialized job, and reads the persisted task for the request.
7. Python pending-PR load/save/clear operations use the same mutex. PR publication acquires it around remote PR creation and local owner-record persistence, and writes the pending-PR record before dispatching long-running CI. Recovery also persists a discovered PR owner record before CI revalidation.
8. After owner merge and exact post-merge validation, the controller clears pending task and pending PR in one mutex-protected transaction. It does not clear either record on a failed merge, changed SHA, failed CI, or lock timeout.

## Failure semantics

- Mutex timeout, API errors, unreadable files, malformed state, or invalid contracts fail closed.
- An abandoned mutex is not treated as proof that state is valid; all state records are validated after acquisition.
- A new manual task is rejected while another autonomous run is queued/active or a pending autonomous PR awaits owner action.
- Atomic replacement remains required for file writes.
- The single-slot task model is explicit: it rejects concurrent submissions instead of pretending to be a multi-item queue.

## Validation

- Unit tests for POSIX lock exclusion, bounded wait, and process-crash release.
- Windows integration test proving PowerShell and Python contend on the exact same named mutex.
- Tests for atomic pending-state clear, pending PR round-trip/corruption rejection, and publication owner record persistence before CI dispatch.
- Windows workflow tests for PowerShell parsing, durable-state mutex interoperability, checkpoint watcher lifecycle, and repository execution-lock contention.
- Exact-head full suite, browser E2E, dependency audit, and target Runner end-to-end acceptance remain required before merge.

## Runner affinity and deployment constraint

The Windows mutex and ProgramData state directory are host-local, not distributed locks. The `codex` job therefore requires the unique `autonomous-state-owner` self-hosted Runner label in addition to `Windows`, `X64`, and `ollama`. `provision_autonomous_runner.ps1` adds that label to the configured Runner and verifies that exactly one registered Runner owns it. The workflow must not be run against multiple unlabelled or independently configured hosts that each have a separate local ProgramData state directory. GitHub's concurrency group serializes jobs; the unique Runner label keeps sequential jobs on the same durable-state host.

## Consequences

- PowerShell and Python now share one explicit state transaction boundary rather than relying on workflow ordering and atomic file replacement alone.
- The single pending-task slot is safer but does not become a queue; callers receive a clear rejection while another run is active.
- The repository execution lock remains separate: it owns the mutable worktree, while this mutex owns durable task/PR state. The checkpoint-store lock separately owns watcher lifetime and checkpoint mutation.
- Local state is deliberately pinned to one uniquely labelled Windows Runner; the named mutex is not represented as a cross-machine distributed lock.
