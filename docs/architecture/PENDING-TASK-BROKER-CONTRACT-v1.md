# Pending-task Broker Contract v1

Status: Implemented contract target; UI remains disabled.

## Transport envelope

Every request is one UTF-8 JSON line, terminated by LF:

```json
{"version":1,"method":"GET","path":"/v1/tasks/pending"}
```

Mutation requests add a bounded `body` object. No other top-level keys are accepted. Responses use the existing envelope:

```json
{"version":1,"status":200,"payload":{}}
```

The Windows named-pipe and POSIX Unix-domain-socket adapters MUST implement identical method/path/body validation and response semantics. Request bodies are limited to 16 KiB; responses are limited to 16 MiB; each request uses one bounded deadline.

## Allow-listed operations

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/v1/tasks/pending` | none | Return safe metadata for pending tasks |
| GET | `/v1/tasks/archived` | none | Return safe metadata for archived tasks |
| POST | `/v1/tasks/archive` | `{ "task_id": "...", "revision": "..." }` | Archive exactly one listed pending task |
| POST | `/v1/tasks/restore` | `{ "task_id": "...", "revision": "..." }` | Restore exactly one archived task |

The existing v1 health, pending-work, progress, diagnostics, and cleanup endpoints remain unchanged.

## Identity and response rules

- `task_id` is an opaque broker-generated identifier. Callers never supply paths, filenames, run IDs as file selectors, or arbitrary state keys.
- `revision` is an opaque snapshot token returned by the same broker listing. The broker re-derives the task identity under the durable-state lock and rejects stale revisions with HTTP-like status 409.
- List responses contain only bounded display metadata: task/run identity, phase/status, last activity, checkpoint verification and sizes, PR reference, and safe blocker codes. They never contain task text, patch/plan bytes, filesystem paths, owner secrets, or arbitrary JSON.
- Unknown, malformed, truncated, oversized, unsupported, or conflicting state fails closed. It is never silently reset or treated as permission to mutate.
- Archive is a soft lifecycle transition. Patch, Plan, checkpoint metadata, owner/audit evidence, and run history are retained byte-for-byte. Permanent purge is not part of this contract.
- Restore re-runs every archive guard and must not create a duplicate pending task.
- The API must independently check GitHub Actions run state before mutation. The broker remains authoritative for local ownership, writer locks, PR marker state, identity, and atomicity.
- Neither broker may expose generic filesystem access, directory enumeration, arbitrary file reads/writes, or a permanent-purge endpoint.

## Safety invariants

1. Archive and restore execute under the existing durable-state lock.
2. Checkpoint writer and repository execution locks are acquired in the established global order; lock contention is a blocker.
3. Missing, unreadable, malformed, stale-unreconciled, or conflicting owner/checkpoint metadata blocks mutation.
4. An open/pending PR blocks archive; the broker never closes or modifies a PR.
5. A changed task identity or revision blocks mutation.
6. Lifecycle ledger updates are schema-versioned, append-only, bounded, and atomically committed with flush/fsync where supported.
7. A failed transition leaves the prior pending/archived state intact and preserves all task artifacts.
8. Broker unavailability and unknown status are fail-closed.

## Rollout boundary

This contract does not activate or modify the dashboard UI. UI work is a later phase after both broker implementations and their cross-platform safety tests pass.
