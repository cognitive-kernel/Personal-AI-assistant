# ADR-022 — Classify recovery failures at the task-execution boundary

- **Status:** Accepted
- **Date:** 2026-11-15
- **Scope:** Autonomous Engineering recovery workflow only

## Context

The recovery workflow previously treated almost every failed step in the Windows `codex` job as a failed autonomous task execution. That included failures in Python discovery, durable task admission, GitHub-state capture, Codex installation, Ollama/model checks, sandbox smoke tests, dependency installation, and post-execution verification.

Those phases do not prove that the coding task started and failed. Counting them toward the task-execution circuit breaker can stop the queue for infrastructure or admission failures. Dispatching a retry for a preflight or post-publication failure can also create a duplicate run while the durable task or an owner-review PR is still present.

## Decision

1. Classify only a failed/timed-out `Execute one isolated task, validate, and publish for owner review` step as a retryable task-execution failure.
2. Classify manual-task persistence rejection separately as `admission_rejected`; it does not increment the execution-failure streak and does not dispatch another run.
3. Classify schedule-gate, prerequisite, setup, sandbox-smoke, dependency-install, pause-gate, and post-execution verification failures as `non_execution`; they do not increment the execution-failure streak and do not dispatch another run.
4. Treat missing, malformed, or ambiguous GitHub job/step evidence as `unknown` and fail closed. A job-level timeout without an identified failed step is not proof that task execution began.
5. Preserve the existing three-consecutive-execution-failure circuit breaker, stale-event ownership checks, bounded backoff, durable task/checkpoint state, and explicit owner-controlled resume behavior.

## Consequences

- Operational readiness failures remain visible as failed workflow/recovery runs but cannot consume the coding-task failure budget.
- Recovery cannot silently bypass a pending manual task or pending owner-review PR.
- The classifier is coupled to the exact workflow step name. A deliberate rename must update the contract test in the same change.
- This classification proves that the controller execution phase failed, not that a Codex model request necessarily began. More granular process-start evidence would require a separate controller-owned event contract and is intentionally outside this bounded workflow fix.

## Required verification

- Unit tests cover admission rejection, schedule-gate failure, each preflight/postcondition boundary, exact task-execution failure, job timeout without step evidence, malformed records, history streaks, stale events, and no-dispatch behavior.
- Exact-head GitHub Actions checks must pass before the PR is considered ready.
- Target Windows Runner execution, crash/restart, and soak evidence remain separate operational acceptance gates.
