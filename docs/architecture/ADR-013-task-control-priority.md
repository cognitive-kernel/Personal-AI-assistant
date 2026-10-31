# ADR-013: Give cooperative pause and cancellation precedence over retry

- Status: Accepted
- Date: 2026-10-31
- Scope: persistent task queue failure settlement

## Context

A task may fail after a user has already requested pause or cancellation but before the handler reaches its next cooperative checkpoint. One concrete path is failure to start the lease-heartbeat thread after the task has been claimed: the worker must settle the claimed task without executing the handler. Previously, `fail_task()` considered only the retry flag and attempt limit. It could therefore change `pause_requested` or `cancel_requested` back to `queued` or `failed`, overriding a committed user control request.

## Decision

- In `fail_task()`, inspect the persisted task status while holding the existing `BEGIN IMMEDIATE` transaction and after validating lease ownership.
- Settle `cancel_requested` to terminal `cancelled`, regardless of the retry flag or remaining attempts.
- Settle `pause_requested` to `paused`, regardless of the retry flag or remaining attempts.
- Only apply existing bounded exponential backoff when the status remains `running`.
- Preserve the failure message for diagnosis and clear the lease owner/expiry in every settled state.
- A paused task remains inert until an explicit resume request; a cancelled task cannot be claimed again.

## Verification

Parameterized task-manager tests cover both control states, and worker-level tests reproduce the heartbeat-thread startup failure after committing a pause or cancellation request. They verify the final state, preserved failure message, lease cleanup, no automatic claim, no handler execution, and explicit resume of a paused task. Existing retry/backoff tests remain unchanged.

## Consequences and limits

- User pause/cancel requests cannot be overwritten by a concurrent failure settlement.
- No database schema, retry-delay, task payload, or worker-handler contract changes are required.
- A failure that occurs before the control request is committed is still governed by the state visible inside the serialized write transaction.
