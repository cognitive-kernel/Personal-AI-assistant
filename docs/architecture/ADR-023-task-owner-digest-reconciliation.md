# ADR-023 — Explicit reconciliation of task-owner digest conflicts

- **Status:** Proposed; exact-head CI and Windows Runner acceptance pending
- **Date:** 2026-11-15
- **Scope:** Durable autonomous task ownership and recovery

## Context

The durable task is stored separately from its owner sidecar. The sidecar binds the exact normalized task digest to the original submission run and current owner run. A digest mismatch means the two records no longer describe the same task. Automatically claiming the task or replacing the sidecar would erase evidence about that mismatch and could transfer work whose identity is uncertain.

The existing fail-closed behavior is correct, but the owner has no explicit recovery operation that distinguishes a failed execution owner from a changed task. A dispatch containing different task text must remain rejected; it is not evidence of a digest mismatch. After GitHub proves the recorded owner run has terminated unsuccessfully and no autonomous PR is awaiting review, ordinary recovery may claim a valid same-digest owner record. The separate owner-authorized repair operation is reserved only for a verified task/owner digest mismatch.

## Decision

1. Keep ordinary task submission, task replacement, and owner-record reconciliation as three distinct operations. The new operation is opt-in through the workflow_dispatch input repair_pending_task_owner; it cannot be combined with a non-empty manual_task or replace_pending_task.
2. Perform reconciliation inside the existing serialized Runner job and shared durable-state lock. Preserve the single-task slot, machine-scoped state root, cross-process lock, active-run gate, and open-autonomous-PR gate.
3. Fetch the exact owner_run_id from GitHub. Rebind only when GitHub confirms the exact recorded run ID and attempt, the autonomous workflow path, main as its head branch, completed status, and a terminal unsuccessful conclusion (failure, cancelled, or timed_out). Unknown, active, successful, inaccessible, or mismatched run metadata fails closed.
4. Never rewrite or replace pending-manual-task.txt during owner reconciliation. Require the existing task and owner sidecar to be present and structurally valid.
5. Archive the exact previous owner-sidecar bytes under a unique quarantine filename before writing the new record. If the replacement write fails, restore the original sidecar. Keep the original submission run and timestamp, increment the claim count, and bind the current workflow run as the new owner.
6. Write schema v2 only for a real digest reconciliation, with current and previous task digests, authorization run ID, the exact reason `owner_authorized_task_content_reconciliation`, and timestamp. The previous and current digests must differ. If they already match, ordinary recovery is the correct path and explicit repair is rejected. Both PowerShell and Python validators enforce numeric run IDs, recovery-only source, a positive immutable authorization attempt, and the digest-difference invariant. Subsequent recovery claims may advance the current owner while preserving the original authorization run ID and attempt.
7. Do not perform this operation automatically during scheduled recovery. The repository owner must explicitly select the workflow input. The operation does not clear checkpoints, task text, Pause state, ACLs, secrets, or pending PR records.

## Consequences

- A verified digest mismatch can be reconciled without abandoning the durable task. Same-digest recovery remains in the ordinary claim path and does not create unnecessary schema-v2 audit metadata.
- The old owner record remains byte-for-byte recoverable in quarantine.
- A completed successful owner run cannot be silently rebound.
- The original task identity remains auditable through the previous digest and quarantine record.
- The workflow remains blocked if GitHub cannot authoritatively verify the prior owner run.

## Validation

Regression coverage includes schema-v2 validation in Python, PowerShell owner-record interoperability, exact quarantine-byte preservation, retention of submission identity/timestamp, and rejection of active or successful owner runs. Exact-head GitHub CI and target-host acceptance are required before this ADR can be marked accepted.
