# ADR-015: Cross-process ownership of durable autonomous task state

- **Status:** Proposed; Windows application-access ACL guidance is superseded by ADR-016
- **Date:** 2026-11-10
- **Scope:** Manual task persistence, pending PR ownership, recovery, and task completion

## Context

The autonomous workflow has one durable manual-task slot and one pending autonomous-PR record. Those files are accessed by different execution paths:

- The PowerShell persistence step runs inside the `codex` job, after it enters the repository-wide GitHub Actions concurrency group.
- The workflow's Python capture step reads the task under the same local durable-state file lock.
- The Python cycle controller loads, saves, and clears pending PR state.
- PR publication occurs in a child process while the controller retains repository worktree ownership.
- After owner merge and exact post-merge CI, the controller clears the task and pending PR.

Atomic file replacement protects an individual write from partial contents, but it does not serialize a read/validate/write transaction across PowerShell and Python. Previously, the PowerShell persistence job ran before the `codex` concurrency group, so two dispatches could race while checking and writing the single task slot. Persistence now runs only after the serialized `codex` job has acquired its concurrency slot. A second dispatch received while the workflow is paused or another run is active fails visibly before it can be accepted; it is not silently queued or written into the active run's slot.

## Decision

1. Keep the canonical machine-aware state-root resolver and OS byte-range lock primitive in the installed `app/autonomous_engineering_state.py` package. The `scripts/autonomous_engineering_state_paths.py`, `scripts/autonomous_engineering_file_lock.py`, and `scripts/autonomous_engineering_state_lock.py` modules are compatibility facades for Runner entry points and preserve the existing script import seams.
2. On Windows, app Python, Runner Python, and PowerShell acquire the same byte-range lock on
   `autonomous-durable-state.lock` in the configured shared state directory. The app package owns the Python platform implementation; the script facades delegate to that exact implementation.
   Python uses `msvcrt.locking`; PowerShell uses `.NET FileStream.Lock`.
   Both use byte offset 0 and length 1, so they contend on the same OS file lock
   across logon sessions and Windows accounts. The OS releases the lock when a
   process exits; a stale lock file is harmless and is never treated as ownership.
3. On POSIX, the same Python API uses a non-blocking `flock` loop with a bounded
   timeout on the same state file. POSIX directory permissions remain a separate
   provisioning concern for participating services. On Windows, the dashboard no
   longer opens or locks the state directory directly; ADR-016 defines the
   LocalService broker boundary. Runner Python and PowerShell still acquire the
   shared byte-range lock directly.
4. Resolve the state root through `app.autonomous_engineering_state.execution_state_directory()` for checkpoint, execution, progress telemetry, and task/PR state. Script compatibility facades delegate to the same resolver. Configured roots must be absolute and outside per-user home directories. Windows uses the machine-scoped configured root or `PROGRAMDATA`; POSIX uses `platformdirs.site_data_path()` and rejects a platform result that resolves inside the current user's home. The legacy POSIX `~/.personal-ai-assistant/autonomous-engineering` tree is copied into the shared root under the durable-state, repository-execution, and checkpoint-store locks. The source is never deleted; conflicting files fail closed and preserve both copies. POSIX directory permissions remain an explicit provisioning responsibility; Windows protected-state ACL ownership and the broker service are defined by ADR-016.
5. PowerShell manual-task persistence runs inside the `codex` job's GitHub concurrency group and holds the shared lock across active-run detection, pending-task/pending-PR validation, open-PR checks, and atomic task write. It rejects a new single-slot task while another autonomous run is queued or active; the schedule gate fails the manual dispatch visibly when paused or occupied. It never silently queues a task that a currently running cycle could later erase.
6. The workflow capture step is read-only for the durable task file. It acquires the shared lock, verifies any supplied task exactly matches the task already persisted by the earlier step in the same serialized job, and reads the persisted task for the request.
7. Python pending-PR load/save/clear operations use the same file lock. PR publication acquires it around remote PR creation and local owner-record persistence, and writes the pending-PR record before dispatching long-running CI. Recovery also persists a discovered PR owner record before CI revalidation.
8. The task text remains a UTF-8 file for compatibility, paired with pending-manual-task-owner.json (schema v1). The owner record stores a SHA-256 digest of the exact task bytes, original submission run/attempt, current owner run/attempt, creation/claim timestamps, claim count, and source. PowerShell persists/validates it through the focused AutonomousEngineeringTaskState.psm1 module; Python capture claims and validates it through autonomous_engineering_pending_task_state.py while holding the same file lock. A recovered task is reassigned only after the workflow gate has established that no other run is active. A missing legacy owner sidecar is migrated under the lock; a mismatched digest or malformed record fails closed.
9. After owner merge and exact post-merge validation, the controller first atomically writes a post-merge cleanup intent (pending-state-cleanup.json), then removes task, owner, and PR records under the file lock, and removes the intent last. If the process crashes during multi-file cleanup, the next PowerShell persistence or Python capture path validates the intent and resumes cleanup idempotently. It does not create this intent on failed merge, changed SHA, failed CI, or lock timeout.
10. The durable-state and checkpoint-store adapters delegate OS locking to `autonomous_engineering_file_lock.py`. They retain distinct lock filenames and ownership scopes: durable task/PR transactions do not implicitly own checkpoint watcher lifetime, and checkpoint ownership does not replace the Python/PowerShell durable-state transaction. Checkpoint operations derive both data paths and the lock path from the same resolved `state["directory"]` value; they must not resolve the global root a second time within the same operation.

## Failure semantics

- File-lock timeout, API errors, unreadable files, malformed state, invalid contracts, and production state-directory permission failures fail closed. Permission failures are installation/ACL failures, not a reason to fall back to per-user state or broaden ACLs. On Windows, dashboard failures are reported through the broker and are not repaired by granting direct state-folder access to app users.
- Unit and integration tests inject isolated temporary state roots; they must never acquire locks under the host's production `%ProgramData%` directory.
- A stale lock file is not proof of ownership; the kernel byte-range lock controls exclusion, and all state records and task-owner leases are validated after acquisition.
- An interrupted accepted-state cleanup is resumed only from a valid schema-v1 cleanup intent. Missing, malformed, or inconsistent task-owner/cleanup metadata fails closed and preserves unknown state.
- A new manual task is rejected while another autonomous run is queued/active or a pending autonomous PR awaits owner action.
- Atomic replacement remains required for file writes.
- The single-slot task model is explicit: it rejects concurrent submissions instead of pretending to be a multi-item queue.

## Validation

- Unit tests for the shared file-lock primitive, POSIX lock exclusion, bounded wait, finite timeout validation, and process-crash release.
- Architecture fitness test requires both ownership adapters to delegate OS-specific locking to the shared primitive while retaining separate lock names.
- Windows integration test proving PowerShell, app Python, and Runner Python contend on the same byte-range lock file.
- Tests for pending task owner lease round-trip, claim/recovery reassignment, task digest mismatch, Python/PowerShell SHA-256 interoperability, pending PR round-trip/corruption rejection, publication owner record persistence before CI dispatch, and interrupted post-merge cleanup recovery.
- Windows workflow tests for PowerShell parsing, durable-state file-lock interoperability, checkpoint watcher lifecycle, and repository execution-lock contention.
- Exact-head full suite, browser E2E, dependency audit, and target Runner end-to-end acceptance remain required before merge.

## Runner affinity and deployment constraint

The Windows file lock and ProgramData state directory are host-local, not distributed locks. The `codex` job therefore requires the unique `autonomous-state-owner` self-hosted Runner label in addition to `Windows`, `X64`, and `ollama`. `provision_autonomous_runner.ps1` adds that label to the configured Runner and verifies that exactly one registered Runner owns it. The workflow must not be run against multiple unlabelled or independently configured hosts that each have a separate local ProgramData state directory. GitHub's concurrency group serializes jobs; the unique Runner label keeps sequential jobs on the same durable-state host.

## Consequences

- PowerShell and Python now share one explicit state transaction boundary rather than relying on workflow ordering and atomic file replacement alone.
- The single pending-task slot is safer but does not become a queue; callers receive a clear rejection while another run is active.
- The task's current owner is explicit and recoverable, while the original submission identity remains auditable across retries. Multi-file post-merge cleanup is resumable through a durable intent record rather than relying on a sequence of untracked deletes.
- The repository execution lock remains separate: it owns the mutable worktree, while the durable-state file lock owns durable task/PR state. The checkpoint-store lock separately owns watcher lifetime and checkpoint mutation.
- Local state is deliberately pinned to one uniquely labelled Windows Runner; the file lock is not represented as a cross-machine distributed lock.
