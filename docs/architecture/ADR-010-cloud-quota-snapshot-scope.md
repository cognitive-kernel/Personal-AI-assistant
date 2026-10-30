# ADR-010: Scope quota snapshots to the active provider consent

- Status: Accepted
- Date: 2026-10-30
- Scope: `app/cloud_resources.py`, Kaggle quota and OCI read-only limit snapshots

## Context

Kaggle quota snapshots are account-specific, while OCI limit snapshots are specific to the selected CLI configuration, profile, region, and compartment. Updating an enabled consent record previously preserved snapshots collected under the previous configuration. The dashboard could therefore display stale account/scope data after a user changed the executable or OCI target.

## Decision

- Treat the full consent configuration as the identity boundary for stored quota/limit snapshots.
- When an enabled consent configuration changes, delete the provider's previous snapshots in the same `BEGIN IMMEDIATE` transaction that records the new consent and updates the configuration.
- Preserve consent audit events; changing configuration records a new `consent_granted` event.
- Repeating an unchanged consent configuration remains idempotent and does not invalidate valid snapshots.
- Revocation continues to disable access, clear stored configuration, delete snapshots, and preserve the audit history.
- Snapshot persistence receives the consent identity captured before the CLI call and re-checks the full scope plus the consent-generation timestamp inside its write transaction. A result from an in-flight request is rejected if the user revokes/re-grants or changes scope while the CLI is running.

## Validation

- Kaggle API regression: collect a snapshot, change the configured CLI path, and verify that the old snapshot is no longer returned.
- OCI API regression: collect a snapshot, change the region, and verify that the old snapshot is no longer returned.
- Existing opt-in, strict-boolean, loopback-only, refresh, revocation, snapshot-validation, and audit-event tests remain required.

## Consequences

A displayed snapshot can no longer silently outlive the account or OCI scope under which it was collected. Users must refresh quota data after changing provider configuration. Historical consent events remain available, while obsolete account-specific measurements are intentionally discarded.
