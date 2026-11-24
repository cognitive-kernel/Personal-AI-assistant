# ADR-024 — Live Pause and execution-mode admission boundaries

- **Status:** Proposed; exact-head CI and target-host acceptance pending
- **Date:** 2026-11-24
- **Scope:** Autonomous Engineering workflow admission, cycle chaining, Resume, and automatic recovery

## Context

The main workflow and recovery workflow previously consumed repository variables through GitHub Actions workflow context. Those values are snapshots taken when a run is created. A run can wait behind the Codex concurrency group while an owner changes the durable Pause or execution-mode setting. The cycle controller also used its startup mode snapshot to decide whether to dispatch the next cycle after an owner merged a PR. Recovery used an environment snapshot and could wait through backoff while Pause or mode changed.

An empty workflow_dispatch was also ambiguous: it could represent an owner-requested Resume or an automatic continuous-cycle chain. Scheduled mode needs to allow a deliberate one-shot Resume without allowing a stale continuous-mode snapshot to create an automatic chain.

## Decision

1. The main workflow performs a live read of the repository Pause and execution-mode variables after acquiring the Codex concurrency slot and immediately before task admission.
2. The Python cycle controller repeats the live reads after acquiring the repository execution lock and before durable-state recovery/task start. After an owner merge, it reads both settings again before dispatching the next continuous cycle.
3. The recovery coordinator reads live execution mode and Pause before classifying a failed run and again after backoff immediately before dispatch. It does not trust workflow-context mode/Pause snapshots.
4. Missing Pause variable (HTTP 404) defaults to false. Missing execution-mode variable defaults to continuous. Any other API failure, malformed response, or unsupported value fails closed and suppresses a new task/recovery/chain dispatch.
5. In scheduled mode, queued push/schedule runs, automatic recovery attempts, and unmarked empty workflow_dispatch runs are skipped. Explicit manual tasks remain allowed.
6. The Resume API marks its empty workflow_dispatch with the boolean input resume_once=true. This authorizes one explicit empty-task run in scheduled mode. Automatic cycle chaining and recovery dispatches never set this marker.
7. A Pause received after a task has started does not terminate or suspend that task. It prevents the next task or automatic recovery from starting after the current safe boundary.
8. The pre-concurrency schedule gate may see a stale workflow-context Pause snapshot. An explicit `workflow_dispatch` with `resume_once=true` may pass that snapshot-only check, but this flag does not authorize execution by itself: the live Pause/mode gate after concurrency admission remains authoritative and must still allow the run.
8. GitHub Actions outputs from the PowerShell gate are appended using UTF-8 without BOM for Windows PowerShell 5.1 compatibility.

## Failure and recovery semantics

- If the live Pause value is true, no new task starts.
- If the live mode is scheduled, automatic push/schedule and recovery runs do not start; manual tasks and an explicitly marked one-shot Resume remain available.
- If live settings cannot be read or are malformed, the gate fails closed. The system does not guess that execution is permitted.
- A missing variable uses only the documented default; it is not treated as an API failure.
- If settings change while a task is already executing, that task reaches its safe boundary. The post-merge live check suppresses the next continuous dispatch.
- If a live check fails after a completed task was accepted, the task remains accepted and the next cycle is simply not dispatched. Durable task/checkpoint state is not cleared.

## Verification

- Workflow contract tests cover live Pause/mode reads, scheduled-mode conditions, resume_once, and BOM-free output.
- Windows CI parses the embedded PowerShell admission step.
- Cycle-controller tests cover post-lock Pause/mode checks, scheduled automatic-run suppression, one-shot Resume, manual-task admission, and post-merge chain suppression.
- Recovery tests cover live mode/Pause, missing-variable defaults, API failures, and settings changes during backoff.
- The Resume endpoint regression verifies resume_once=true; automatic chaining tests verify the marker is absent.
- Exact-head Linux/Windows tests, Windows storage regressions, Browser E2E, CodeQL, and dependency audit are required. A target-host Windows run remains separate acceptance evidence.

## Consequences

- Repository settings take effect at the next safe admission boundary rather than only for newly created workflow runs.
- Scheduled mode has an explicit distinction between owner-requested one-shot Resume and automatic chaining.
- An unavailable settings API can temporarily stop autonomous progress. This is intentional fail-closed behavior; the owner can retry after the GitHub API becomes available.
- No task, checkpoint, Runner registration, ACL, repository variable, or active workflow is modified by the gate itself.
