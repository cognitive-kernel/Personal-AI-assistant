# Controlled experience test runner

## Purpose

The runner connects a versioned experience record to actual Python execution in the existing Docker sandbox. It is available only when the administrator-configured sandbox is enabled and the user has current sandbox consent.

## Preconditions

- The experience is not already verified.
- At least one HTTPS source-evidence item is stored.
- The selected test name exactly matches an item in the saved test plan.
- The submitted Python harness parses successfully and contains at least one `assert` statement.
- The user confirms the specific execution request, and the persistent sandbox-consent record is enabled.
- Docker executable and Python image are administrator-configured; the image must be pinned by SHA-256 digest.

## Execution and evidence

The task uses the existing sandbox handler, which enforces network isolation, read-only root filesystem, non-root execution, resource/time/output limits, and periodic consent/cancellation checks. The source code is stored in the task payload for execution but is not copied into the experience record. The result stores the source SHA-256, pinned image, execution status, exit code, duration, and bounded stdout/stderr evidence.

A successful sandbox execution with exit code 0 promotes the experience to `verified` only when source evidence and a matching test-plan item still exist at execution time. A non-zero/failed execution records a failed result and keeps the experience unverified. Each result is versioned with the experience history. The runner does not claim universal correctness: the submitted harness must actually test the named claim, and the user must review it before execution.

## Limits

- Python only; no network access inside the container.
- Dependencies must already be present in the pinned image; no automatic package installation.
- One execution attempt per task; failed/expired tasks are not automatically rerun.
- A verified record is immutable through the public API; create a new experience to test a changed claim.
- If the sandbox is not configured or consent is revoked, execution is unavailable or stopped.
