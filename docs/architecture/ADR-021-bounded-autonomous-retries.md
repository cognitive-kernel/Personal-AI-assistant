# ADR-021 — Bounded autonomous task retries and command deadlines

- Status: Proposed
- Date: 2026-11-14
- Scope: Autonomous Engineering controller runtime

## Context and evidence

The Codex child process now has a 30-minute hard deadline, but the controller's task-repair loop still used an unconditional `while True`. A failed Codex run, missing completion marker, local test failure, or exact-head CI failure immediately started another fresh Codex session for the same task. The only outer bound was the GitHub Actions job timeout of six hours.

The same controller helper invoked Git, tests, artifact checks, and other Python tools through `subprocess.run` without a timeout. Those commands could therefore occupy the remainder of the six-hour job even when the Codex process itself was bounded.

The recovery workflow already preserves the pending task and checkpoint, and dispatches another owner-controlled run after bounded exponential backoff. That recovery path is the correct place to continue unfinished work; an individual runner should not spend its entire six-hour lease repeating the same failed attempt.

## Five-why analysis

1. A workflow could remain active for nearly six hours because the task loop had no attempt-count or task-runtime limit.
2. Each failed attempt immediately launched another Codex session instead of yielding to durable recovery.
3. The 30-minute Codex deadline bounded one invocation, not the total task retry loop.
4. Verification and helper subprocesses had no independent command deadlines.
5. Existing tests verified individual process timeout and recovery primitives, but did not assert a bounded end-to-end retry policy for the controller loop.

## Decision

1. Introduce a pure `RetryPolicy` contract in `scripts/autonomous_engineering_retry_policy.py`; keep policy separate from orchestration.
2. Permit at most two Codex attempts and at most two hours of task/retry time per workflow run, whichever limit is reached first. The remaining budget also caps Codex, verification, and PR-validation subprocess deadlines; no long phase may start after the budget is exhausted.
3. When the budget is exhausted, fail the current run without clearing pending task state, task branch, or any checkpoint. The existing recovery workflow can resume the same task in a later run.
4. Give controller subprocesses explicit deadlines: five minutes by default, fifteen minutes for the full Python suite, twenty-five minutes for exact-head PR publication/validation, and thirty seconds beyond the Codex runner's own thirty-minute deadline for its parent wrapper.
5. Convert command timeout into a named, bounded diagnostic error. Never interpret timeout as success.
6. Preserve the six-hour GitHub job ceiling as a final platform safety boundary, not as the normal task retry budget.
7. Open a recovery circuit breaker after three consecutive **controller task-execution failures**. Count only failures in the serialized `codex` job after task admission; do not count schedule-gate rejections or failures in the manual-task persistence step, because those runs never entered task execution. A successful run resets the streak; admission-only failures are excluded without resetting it. If a failed run cannot be classified from its job/step contract, fail closed and do not dispatch. When the breaker opens, publish an explicit failed conclusion and job summary rather than returning success. Preserve the durable task/checkpoint and require an owner to inspect the first failing execution and explicitly resume after correction.

## Consequences and acceptance

- A single workflow run cannot repeatedly consume the full six-hour window on one failing task; the two-attempt ceiling leaves room for full verification and exact-head CI within the two-hour task budget.
- The same task remains the only task in scope; recovery does not advance the task ledger or discard partial work.
- Each command has a local deadline, and Codex retains its OS-level descendant ownership boundary from ADR-020.
- Tests must cover policy boundaries, invalid policy inputs, controller retry exhaustion, checkpoint preservation, and subprocess timeout reporting.
- Exact-head CI and a real target-Runner smoke/soak remain required before operational acceptance. This ADR does not change the durable Pause setting or enable task dispatch.


## 2026-11-15 — Recovery failure classification correction

The recovery workflow previously counted every failed or timed-out workflow run, including a rejected manual-task submission. Run [43695676192](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43695676192) failed in the `Persist manual task under the controller's runner and concurrency ownership` step with `Another manual task is still pending`; the `codex` controller step was skipped. No autonomous task execution failed in that run. The previous history-based counter nevertheless included it, and the breaker path exited with status 0, making an open/stopped recovery lane appear green.

The recovery policy is now a separately testable module. It classifies failures from GitHub's structured job/step results, excludes schedule-gate and manual-task admission rejections from the execution streak, counts only controller execution failures, and blocks on unknown classifications. The workflow checks out trusted `main` with read-only contents access and invokes the policy module. A blocked or open breaker writes a job summary and an error annotation, and exits non-zero. No durable task, owner record, checkpoint, Pause setting, Runner identity, or ACL is modified by the classifier.

Acceptance requires the new policy unit tests, recovery workflow contract tests, full Python CI, exact-head workflow checks, and the owner's local Windows full-suite rerun where available. The historical open-breaker run [43695720003](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43695720003) remains evidence of the old behavior, not proof that the corrected policy is deployed.
