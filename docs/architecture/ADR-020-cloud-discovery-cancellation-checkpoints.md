# ADR-020 — Cloud discovery cooperative cancellation across separate stores

- Status: Accepted
- Date: 2026-11-01
- Workstream: E-05 durable task resilience

## Context

The `cloud.discover_resources` handler already passes the durable task checkpoint callback into `discover_resources`. Before this change, checkpoints occurred only before each search query. A pause/cancel arriving during the final query could therefore remain unobserved while the handler proceeded through official-reference fallback and candidate persistence. Candidate upserts are written to `cloud_resources.db`, while the task queue and pause/cancel state live in `learning.db`; they cannot participate in one SQLite transaction.

## Decision

- Checkpoint after each query has been processed, including the final query.
- Checkpoint before and after each official-reference fetch.
- Checkpoint before each candidate upsert and immediately before the candidate batch commit.
- Checkpoint again before finalizing the discovery-run record.
- Keep all candidate upserts in the existing single cloud-store transaction. If pause/cancel wins before its commit, closing the connection rolls back the candidate batch.
- Track the committed candidate count so cancellation after the cloud-store commit records an accurate partial run rather than claiming zero writes.
- Preserve the daily scheduling chain when discovery is paused, cancelled, or fails.

## Consequences and limitations

- Pause/cancel is cooperative at explicit boundaries; an in-flight provider request cannot be interrupted by this callback.
- If control arrives after the cloud-store transaction commits, candidate upserts remain. They are keyed by normalized source URL/content evidence and are safe to refresh; the run record is marked partial with the committed count. This is an explicit cross-database limitation, not a claim of distributed atomicity.
- No schema migration or new dependency is required.
- The regression matrix injects both pause and cancel at the final-query, pre-commit, and post-commit/finalization boundaries, verifying task state, candidate persistence, partial-run accounting, and daily-chain continuation.
