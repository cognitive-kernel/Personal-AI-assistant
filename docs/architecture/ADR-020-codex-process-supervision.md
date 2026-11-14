# ADR-020 — Owner-bound Codex process supervision

- Status: Proposed
- Date: 2026-11-14
- Scope: Autonomous Engineering Codex/Ollama execution

## Context

The autonomous controller already has distinct ownership boundaries for the GitHub Actions concurrency group, repository worktree lock, durable task/PR state mutex, and checkpoint-watcher lifecycle. Those locks serialize state transitions and protect checkpoint mutation, but an operating-system lock held by a parent process is released when that process exits. A Codex child process that outlives its controller could therefore continue writing to the worktree after the repository lock becomes available to a recovery run.

The Codex stream configuration also allowed a 15-minute idle interval, ten stream retries, and four request retries. The sandbox smoke invocation had no independent process deadline. This made a stalled provider stream expensive and allowed a diagnostic run to occupy the Runner far longer than the operation it was meant to verify.

## Five-why analysis

1. **Why could an old Codex process still write after a run owner failed?** Because the Codex child process could outlive the Python process that launched it.
2. **Why could it outlive the owner?** The launch path used a normal subprocess and waited for streamed output; it had no OS-level process-tree ownership boundary or hard total deadline.
3. **Why did the repository lock not prevent this?** The worktree lock is owned by the controller process. The operating system correctly releases that lock when the controller exits, but that lock does not automatically terminate unrelated child processes.
4. **Why was this not caught by the existing recovery architecture?** Existing tests covered mutex exclusion, checkpoint recovery, and watcher identity, but did not inject a controller/supervisor crash while a Codex descendant remained alive.
5. **Why could a smoke test consume so much Runner time?** Provider configuration allowed a 900,000 ms idle wait with 10 stream retries and 4 request retries, while the smoke subprocess had no independent wall-clock budget.

**Evidence:** `scripts/autonomous_engineering_progress.py` starts the Codex command through `subprocess.Popen` and consumes its output stream; `scripts/autonomous_engineering_codex_config.py` supplied the long idle/retry values; the smoke call used the same Codex execution path without a per-process deadline. The worktree/state/checkpoint locks were already separate and tested; the missing invariant was ownership of the complete Codex process tree.

## Decision

1. Keep the existing task-state mutex, worktree lock, checkpoint watcher, and PR-state ownership modules separate. Do not replace their contracts with one global lock.
2. Route Codex through a small process-supervisor module. It records the PID and OS creation time of the owning controller ancestry and refuses to start if that identity cannot be verified.
3. The supervisor enforces a hard wall-clock budget. A sandbox smoke is limited to 300 seconds; a normal engineering Codex session is limited to 1,800 seconds.
4. On Windows, the supervisor assigns itself to a kill-on-close Job Object and duplicates a handle into its owning progress process. Codex and all descendants inherit job membership automatically. The supervisor monitors the recorded owner ancestry and uses TerminateJobObject on timeout, owner loss, and final cleanup. If the supervisor crashes, the owner-held handle keeps the job alive until the progress owner exits; closing the final handle then terminates any surviving descendants.
5. On POSIX hosts, the Codex process starts in a new process group; timeout or owner loss terminates and reaps that group.
6. Use a short smoke-specific stream idle timeout and no provider retries. Normal engineering sessions retain a larger but bounded retry budget.
7. If parent identity, process termination, or process reaping cannot be established, fail closed. Do not treat a timeout as a successful smoke or task.

## Consequences

- A controller crash cannot leave an unowned Codex process writing after its supervisor detects owner loss; the OS process job is the final descendant cleanup boundary.
- Checkpoint-watcher ownership remains independent. The watcher is not placed in the Codex Job Object and retains its existing run/task/base-SHA/process-start identity contract.
- Smoke failures become bounded and diagnosable instead of waiting through repeated long stream retries.
- The supervisor adds a process boundary and a focused test surface; it does not alter task payloads, checkpoint contents, Git history, user data, or PR merge policy.
- CI must verify timeout behavior, descendant cleanup, PID-reuse rejection, existing lock exclusion, and existing checkpoint recovery. A real target-Windows Runner soak remains an external acceptance gate.


## 2026-11-14 addendum — Windows sandbox setup mutex and non-admin Runner

A target-Runner smoke exposed a second, independent failure below the Codex process supervisor:

`Failed to create unified exec process: helper_unknown_error: open sandbox setup mutex`.

The Runner is intentionally a non-administrator service identity. The pinned Codex CLI's upstream Windows sandbox helper creates the machine-wide mutex `Global\CodexSandboxSetup` with the protected security descriptor `D:P(A;;GA;;;SY)(A;;GA;;;BA)`; the upstream source documents that this lock is for LocalSystem or an elevated administrator. A normal non-admin Runner identity is not granted access by that descriptor. This is an upstream helper authorization contract, not a stale application mutex, a Python worktree-lock conflict, or a reason to elevate the Runner.

Primary evidence:
- [Codex setup_mutex.rs at rust-v0.160.0](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/windows-sandbox-rs/src/setup_mutex.rs) defines the mutex name, descriptor, and identity contract.
- [Codex issue #50163](https://github.com/openai/codex/issues/50163) records the same failure under a non-administrator service identity after successful provisioning.
- Run 43592197032 confirms the exact helper error in the elevated backend. Its standalone MXC compatibility probe passed. The earlier MXC agent smoke did not prove sandbox failure: its Python `-c` payload was malformed for the requested `cmd.exe` shell and generated repeated syntax/command errors before the elevated attempt.

Corrective changes in PR #757:
1. Use a shell-native `cmd.exe` create/read/delete probe (`echo`, `type`, `del`) so the smoke test validates the intended workspace operation without Python quoting ambiguity.
2. Keep MXC as the first candidate and allow the restricted-token `unelevated` implementation as a separately smoke-tested fallback.
3. Do not select the elevated helper for the non-admin Runner by default; it is incompatible with the observed mutex ACL contract. Do not make the Runner an administrator, change the upstream helper ACL, or fall back to unsandboxed execution.
4. Bound the workflow smoke step independently at 15 minutes and classify this mutex error explicitly in sanitized diagnostics.

**Acceptance boundary:** repository CI can validate argument construction, diagnostics, and process supervision. Only a successful real `codex exec` workspace create/read/delete smoke on the target Runner can establish which sandbox backend is operational. The target-host smoke remains pending while the durable autonomous-engineering pause is enabled. No pause setting, task/checkpoint data, host ACL, Runner identity, or production Codex installation was changed by this PR.
