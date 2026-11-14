# ADR-020 — Owner-bound Codex process supervision

- Status: Proposed
- Date: 2026-11-14
- Scope: Autonomous Engineering Codex/Ollama execution

## Context

The autonomous controller already has distinct ownership boundaries for the GitHub Actions concurrency group, repository worktree lock, durable task/PR state mutex, and checkpoint-watcher lifecycle. Those locks serialize state transitions and protect checkpoint mutation, but an operating-system lock held by a parent process is released when that process exits. A Codex child process that outlives its controller could therefore continue writing to the worktree after the repository lock becomes available to a recovery run.

The Codex stream configuration also allowed a 15-minute idle interval, ten stream retries, and four request retries. The sandbox smoke invocation had no independent process deadline. This made a stalled provider stream expensive and allowed a diagnostic run to occupy the Runner far longer than the operation it was meant to verify.

## Decision

1. Keep the existing task-state mutex, worktree lock, checkpoint watcher, and PR-state ownership modules separate. Do not replace their contracts with one global lock.
2. Route Codex through a small process-supervisor module. It records the PID and OS creation time of the owning controller ancestry and refuses to start if that identity cannot be verified.
3. The supervisor enforces a hard wall-clock budget. A sandbox smoke is limited to 300 seconds; a normal engineering Codex session is limited to 1,800 seconds.
4. On Windows, the supervisor assigns itself to a kill-on-close Job Object and duplicates a handle into its owning progress process. Codex and all descendants inherit job membership automatically. The parent-held handle makes the OS close the job when the owner exits; the supervisor also uses TerminateJobObject for timeout, owner loss, and final descendant cleanup.
5. On POSIX hosts, the Codex process starts in a new process group; timeout or owner loss terminates and reaps that group.
6. Use a short smoke-specific stream idle timeout and no provider retries. Normal engineering sessions retain a larger but bounded retry budget.
7. If parent identity, process termination, or process reaping cannot be established, fail closed. Do not treat a timeout as a successful smoke or task.

## Consequences

- A controller crash cannot leave an unowned Codex process writing after its supervisor detects owner loss; the OS process job is the final descendant cleanup boundary.
- Checkpoint-watcher ownership remains independent. The watcher is not placed in the Codex Job Object and retains its existing run/task/base-SHA/process-start identity contract.
- Smoke failures become bounded and diagnosable instead of waiting through repeated long stream retries.
- The supervisor adds a process boundary and a focused test surface; it does not alter task payloads, checkpoint contents, Git history, user data, or PR merge policy.
- CI must verify timeout behavior, descendant cleanup, PID-reuse rejection, existing lock exclusion, and existing checkpoint recovery. A real target-Windows Runner soak remains an external acceptance gate.
