# ADR-017: Safe lifecycle management for pending autonomous tasks

- Status: Proposed
- Date: 2026-11-22
- Decision owners: repository maintainers

## Context

The Autonomous Engineering dashboard exposes a summary of pending work and a resume action, but it does not provide an inventory or a safe way to retire a stale task. Pending work may consist of a manual-task marker, owner metadata, an open/pending PR, a checkpoint pointer, checkpoint metadata and patch, or a plan. These records are durable recovery inputs, not disposable UI cache. Deleting files directly from the dashboard would bypass the Runner ownership protocol and could destroy the only recoverable copy of uncommitted work.

The state store is accessed through two platform-specific restricted brokers: the Windows named-pipe service and the POSIX Unix-domain-socket service. The application must not gain direct filesystem access or a generic path-based broker operation.

## Decision

### 1. Ordinary removal is an archive operation

The primary UI action is **«کنارگذاشتن وظیفه»** (archive/discard from the pending queue), not permanent deletion.

- It marks one exact, verified task identity as abandoned in a durable lifecycle ledger.
- It removes only the task's active admission/blocking state after all safety checks pass.
- It preserves Patch, Plan, checkpoint metadata, owner/audit evidence, and run history byte-for-byte.
- The archived task remains visible in a separate archived view and can be inspected. It is not automatically resumed.
- Archiving is reversible only through an explicit restore operation, and restore must revalidate all current ownership, checkpoint, and PR conditions before making the task pending again.
- The UI must explain that archived artifacts are retained and do not count as an active pending task.

This is the default because it resolves a stale queue blocker without destroying the only copy of recoverable work.

### 2. Permanent purge is a separate, deferred operation

Permanent removal of Patch/Plan/checkpoint artifacts is not part of the initial management feature. It must not be exposed as an ordinary delete button. A future purge operation requires a separate explicit confirmation naming the exact task identity and artifact set, a retention policy, an audit record, and a second validation under the durable-state lock. Unknown or inconsistent state is never purgeable.

### 3. Stable task identity and lifecycle ledger

Each managed item must have an immutable identity derived from validated state, not from a UI-supplied path. The identity includes the task/run identity and a digest of the persisted task content or checkpoint metadata. The broker returns only safe display metadata and opaque identifiers; it never returns filesystem paths or patch contents.

The broker maintains an append-only lifecycle/audit ledger with schema versioning. An archive transition records the exact identity, timestamp, actor/source, prior state, and reason. Writes use a temporary file, flush/fsync where supported, and atomic replacement while holding the existing durable state lock. Malformed, truncated, oversized, or unsupported ledger data causes a fail-closed response; it must not be silently reset.

### 4. Mandatory archive guards

The broker refuses an archive request unless all conditions are satisfied while holding the same durable-state lock used by task/checkpoint writers:

1. The state root and broker are healthy, and the lifecycle ledger is readable and valid.
2. The submitted opaque task ID resolves to exactly one currently pending item; the broker re-derives and compares the complete identity. No caller-supplied filename or path is accepted.
3. No GitHub Actions run is queued, waiting, requested, pending, or in progress for the task/repository.
4. No checkpoint writer or execution owner holds the relevant lifetime lock; owner metadata is valid and proves that the task is not currently owned. Missing, unreadable, stale-but-unreconciled, or conflicting owner metadata is a blocker, not permission.
5. No open or pending PR is associated with the task. A PR must be handled through the existing GitHub review/close workflow first; archiving must not close or modify a PR.
6. The task/checkpoint identity has not changed between listing and mutation.
7. The transition can be committed atomically. If commit fails, the previous pending state remains intact.

The API must independently verify there is no active GitHub run before requesting the broker mutation. The broker remains authoritative for local state and locks; the API cannot override broker refusals.

### 5. Cross-platform broker contract

Windows and POSIX brokers expose the same versioned, allow-listed operations:

- list pending and archived items (safe metadata only);
- archive one exact opaque task ID;
- restore one archived item after full revalidation.

No generic filesystem operation, caller-provided path, arbitrary filename, raw patch read, or permanent purge operation is added. Both client allow-lists and both service dispatchers must be updated together. The existing progress cleanup endpoint remains unrelated and must continue preserving all task/checkpoint artifacts.

### 6. UI behavior

The Persian dashboard receives a dedicated **مدیریت وظایف معلق** section with pending and archived tabs. Each item shows available verified metadata only: task identity/summary, run and task number, phase/status, last activity, checkpoint verification, patch/plan presence and size, PR number/URL, and explicit blocker reasons.

- Resume is available only for a verified, resumable item and uses the existing dispatch/recovery path.
- Archive requires a confirmation dialog naming the exact item and explaining that Patch/Plan/checkpoint artifacts are retained.
- An item with an active run, active owner/writer, open PR, unknown checkpoint, invalid metadata, or broker error is read-only and displays the exact safe blocker.
- After mutation, the UI performs an authoritative refresh; it never optimistically removes an item.
- All controls are keyboard accessible and expose status/errors to assistive technology.

## Consequences

- A stale task can be removed from the active queue without destroying recovery artifacts.
- Users can inspect what was retained and why a task cannot be archived.
- The system has an auditable lifecycle rather than relying on deletion of unrelated marker files.
- The feature requires coordinated changes to both broker implementations, both broker clients, the API, UI, and tests. It is not complete until cross-platform broker contract tests, API tests, UI tests, and Windows/POSIX validation pass.

## Validation requirements

- Verify listing never exposes paths or patch contents.
- Verify archive preserves every Patch, Plan, checkpoint, owner, PR, and history artifact byte-for-byte.
- Verify archive is refused for active runs, active checkpoint writers, valid task ownership, open PRs, malformed/unknown checkpoint state, identity mismatch, and broker lock timeout.
- Verify a failed/partial ledger write leaves the task pending and can be recovered without losing data.
- Verify archived tasks no longer block a new task, while their artifacts remain intact.
- Verify restore cannot create duplicate active tasks and rechecks all current guards.
- Run identical broker contract tests against Windows and POSIX adapters.
