# ADR-025: Native state ownership and service-free Autonomous Engineering runtime

- **Status:** Proposed; implementation and target-host acceptance are required before adoption.
- **Date:** 2026-11-26
- **Scope:** Autonomous Engineering startup, Runner lifecycle, durable state, execution locks, and stale-run recovery on Windows and Linux.

## Context and verified defects

The current Windows path has a machine-wide state broker implemented as a Windows Service. Provisioning is performed by PowerShell, compiles `AutonomousStateBrokerService.cs` on the target host using the .NET Framework compiler, installs/configures the service as `NT AUTHORITY\\LocalService`, and changes machine-level environment variables and filesystem ACLs. The setup path requires elevation and discovers/configures Runner service identities and SIDs. This is incompatible with ordinary-user, no-manual-group, no-fixed-identity operation.

The Python application communicates with the Windows broker through a named pipe. POSIX uses a Unix-domain-socket broker with machine-wide configuration. The two transports therefore depend on separately provisioned broker processes and do not provide one application-owned lifecycle.

The current cleanup policy also conflates durable recovery material with live execution:
- A present checkpoint is treated as a cleanup blocker even when its watcher/store lock is no longer held. A checkpoint is recovery material and must be preserved, not treated as proof of a live execution.
- A non-terminal `current.json` status is treated as an active run without proving that the corresponding controller still owns the repository lock or that the exact recorded process is alive.
- Repository lock metadata records PID, run ID, attempt, host, and acquisition time, but omits OS process creation time. PID reuse therefore cannot be distinguished reliably from the original owner using the lock record alone.

These defects explain why stale progress and checkpoint metadata can produce a permanent `AUTONOMOUS_ENGINEERING_CONFLICT` even after execution has stopped. They do not prove that the user's current machine has a live process.

## Decision

### 1. One native Python state implementation

Replace the Windows C# service, PowerShell provisioning path, Windows named-pipe client, and POSIX broker daemon with one Python-owned state adapter used by the app and Runner. The application and its local Runner must execute under the same dynamically discovered interactive account; no username, SID, group, service identity, fixed profile, or Administrator membership is part of the ownership protocol.

- Windows and Linux use the existing OS-managed cross-process file-lock adapter in `app/autonomous_engineering_state.py` (Windows byte-range locking; POSIX `flock`).
- Keep separate lock scopes and filenames for repository execution, durable task/PR transactions, dispatch admission, and checkpoint-store/watcher ownership. Do not collapse them into one lock.
- Lock ownership is an opaque run ID + attempt + fencing epoch/token, not an OS account name. Add PID and OS process creation time only as diagnostic process identity evidence; PID alone is never sufficient.
- Keep state under the OS application-data location discovered at runtime. The app and its child Runner share the same process identity and state root. Do not require machine-level registry/environment configuration or ACL edits.
- Start the self-hosted Runner as an ordinary child/background process only when the user enables the remote execution capability. Do not install it as a Windows Service or create local accounts/groups. Discover its installation path; do not assume a fixed checkout or install directory.
- If the required Runner cannot run in the current user's session, report only remote execution as unavailable. Do not elevate, mutate machine state, or break the local dashboard.

### 2. Safe migration from the legacy broker

Legacy ProgramData / machine-configured state may contain pending tasks, PR ownership, checkpoint metadata, and run history. Migration is additive and non-destructive:

1. Discover the legacy state root read-only.
2. Prove that the repository execution lock and checkpoint-store lock can be acquired and that no corresponding GitHub workflow is queued/in progress before copying.
3. Acquire repository -> durable-state -> checkpoint-store locks in the established order.
4. Copy validated files into the new state root using atomic temporary files; compare hashes and schema. Preserve both copies.
5. On conflicting content, unreadable metadata, ambiguous process identity, unavailable legacy ACLs, or a lock timeout, stop migration and preserve all files. Never silently choose one copy.
6. Do not remove the legacy service, old directory, old lock files, or ACLs as part of automatic migration. Retirement is a separate, explicitly verified operation after the new path has passed target-host acceptance.

### 3. Run ownership and recovery state machine

The authoritative execution evidence is the conjunction of:
- Exact workflow run ID and attempt, fetched from the configured GitHub workflow where the operation is remote.
- The OS repository execution lock state.
- The lock owner's run ID, attempt, fencing token/epoch, host, PID, and process creation time.
- Exact process identity evidence (PID + creation time + expected controller command identity) when the process is inspectable.
- The independent checkpoint-store lock and watcher metadata for checkpoint ownership.

Classify a run as:
- **active:** the repository lock is held by the matching run owner, or the exact process is proven alive; preserve all state and refuse cleanup.
- **stale:** the repository lock is free, the exact recorded process is absent or PID-reused, and the authoritative remote run is terminal (or the run is explicitly local and its exact process is proven absent). Under the established lock order, atomically append an interruption/recovery event and transition only the matching progress projection to `interrupted`. Preserve the original record in history and preserve every checkpoint and pending task/PR record.
- **unknown:** host mismatch, access denied, unreadable lock metadata, process identity ambiguity, remote status unavailable, or any lock timeout. Fail closed and preserve all state; do not label it active merely because a status string is non-terminal.

A checkpoint's existence is not evidence that a process is active. The checkpoint-store lock and exact watcher identity determine whether a watcher is active. A valid stale checkpoint remains available for controller-owned recovery. Recovery must continue to obey ADR-014: controller owns the repository lock, watcher owns only checkpoint-store lifetime, and restore validates base SHA and `git apply --check` before applying any patch.

### 4. Cleanup contract

- Cleanup must never delete checkpoint files, checkpoint metadata, pending task/PR records, owner/fencing records, or the authoritative current-run record.
- A verified stale run must not permanently block cleanup of expendable telemetry. Append a recovery event and retain an `interrupted` projection rather than erasing history.
- A truly active run or watcher must remain protected by its OS lock.
- Unknown ownership must remain blocked with a structured reason and correlation ID, not a generic permanent conflict.
- Cleanup and recovery must be idempotent and safe after process restart.

## Compatibility and rollout

- Keep existing public API response fields stable where possible; add structured ownership classification and safe details.
- Introduce the native state adapter behind a capability flag during migration. Do not silently fall back from a failed native state path to the legacy broker or create two authoritative state roots.
- Preserve the current Windows broker and legacy data read-only until migration, native Runner, crash/restart, and recovery acceptance all pass. Do not uninstall or alter the user's existing service/Runner from CI.
- Remove PowerShell only from the Autonomous Engineering runtime/startup/Broker communication path. PowerShell may remain as an optional operator tool outside the runtime only if it is not required by the supported workflow.

## Required tests and acceptance evidence

1. Windows non-admin startup with no PowerShell executable, no machine environment variables, no service installation, and no manual ACL/group/account changes.
2. Linux startup using the same state and ownership contract.
3. Cross-process lock exclusion on Windows and Linux; lock is released after process death; a stale lock file alone is never treated as a held lock.
4. PID reuse and process creation-time mismatch are classified as stale only when remote run state is terminal and all relevant locks are free.
5. Access denied, host mismatch, malformed metadata, remote status outage, and lock timeout are classified unknown and preserve state.
6. A stopped run with non-terminal `current.json` is reconciled to `interrupted` and no longer causes permanent `AUTONOMOUS_ENGINEERING_CONFLICT`.
7. A live controller holding the repository lock and a live watcher holding the checkpoint-store lock both prevent cleanup.
8. A verified stale checkpoint does not block telemetry cleanup and is byte-for-byte preserved.
9. Recovery with a mismatched run ID, attempt, fencing token, base SHA, patch digest, or watcher identity is rejected without deleting or applying the checkpoint.
10. Browser/API end-to-end tests prove status, dispatch, progress, pause/resume/cancel, restart, and recovery continue to reflect authoritative state.
11. Exact-head Windows and Linux CI plus non-admin Windows target-host acceptance are required before removing the legacy broker or merging.

## Consequences

This design removes the mandatory Windows Service, UAC provisioning, PowerShell dependency, machine-wide state registry configuration, and hard-coded service-account ACL choreography from ordinary Autonomous Engineering operation. It retains OS-native process locks and the existing task/lease/checkpoint safety contracts. Remote execution becomes unavailable rather than unsafe when an ordinary-user Runner cannot be started or verified.
