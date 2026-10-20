# Roadmap acceptance status

Last reviewed: 2026-10-20

This document records repository-level evidence and the remaining acceptance gates. A merged implementation or passing CI suite does not by itself establish production readiness on a user's machine.

Repository status last reconciled on 2026-10-20 at `bd4267f069c0b469541ddc0e16303fa83a9bc597`. Task dashboard summary/search ([PR #356](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/356)), safe bulk operations ([PR #357](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/357)), semantic task status styling ([PR #358](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/358)), current-section navigation ([PR #360](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/360)), accessible learning-path progress ([PR #362](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/362)), and contextual task empty states ([PR #363](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/363)) are merged. Post-merge main CI passed on `bd4267f069c0b469541ddc0e16303fa83a9bc597`: [tests and Windows storage regressions](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408060), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408011), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408061). Target-host, live-model, manual accessibility, staging/rollback, and independent security acceptance remain open.

## Current status

| Stage | Status | Evidence and remaining gate |
|---|---|---|
| Repository audit and baseline | Partial | A redacted target-host baseline tool now records commit/runtime, read-only SQLite health, and optional full pytest counts. The tool still must be run on the actual target host; complete checkout and broader supply-chain audit remain; a scheduled `pip-audit` workflow now checks the resolved Python environment on pull requests, main pushes, and weekly. |
| Module boundaries and contracts | Partial | The merged AST-based audit (`scripts/check_import_cycles.py`) scans 55 Python modules and found no eager module-time import cycles on its checked head. Dynamic/runtime dependencies and architecture-level refactoring still require broader review. |
| Persistent task execution | Implemented with bounded evidence | SQLite queue, leases, checkpoints, cancellation, recovery, thread-race tests, a six-process independent-connection claim test, an abrupt worker-process termination/checkpoint-recovery test, and an isolated concurrent-enqueue/claim-complete benchmark are covered by CI. The allow-listed `TaskRuntime` is wired into FastAPI startup/shutdown; learning jobs are reconciled and enqueued idempotently, and the schema registry validates startup before worker activation. The benchmark now rejects paths aliasing configured application SQLite stores and rejects identical settings/learning paths before initialization. Run `python scripts/benchmark_task_queue.py --tasks 1000 --producers 4 --workers 4 --output reports/task-queue-benchmark.json` on the target host and archive the report. Target-host crash/restart, storage fault injection, sustained load, and recovery across every task type remain. |
| Research and learning | Implemented with limitations | Source-backed research, provenance, source-conflict reporting, local quality diagnostics, learning paths, recommendations, spaced review, and advisory semantic review exist. Live Ollama runs and human adjudication on a versioned evaluation set remain necessary before claiming knowledge quality. |
| Isolated execution | CI-validated | Docker sandbox and self-improvement test runner use restricted containers; CI includes real Docker integration with a pinned image. Target-machine installation and operational validation remain. |
| Experience memory | Implemented with limitations | Versioned experiences, explicit capture consent, source checks, freshness-aware hybrid retrieval, explicit feedback, and controlled verification exist. Retrieval relevance still needs a broader adjudicated evaluation set. |
| Local operations UI | Partial | Chat/task controls, cursor-paginated task listing, durable task status/priority/schedule audit history (PR #347), interactive cursor-paginated audit history (PR #351), learning, experience review, storage health, backup/restore staging, and self-improvement controls exist. Real Chromium CI covers dashboard loading, storage health, recommendations, experience creation, consent revocation after reload, experience review persistence without implicit verification, backup-to-staging-restore, cancellation of a persisted scheduled task, persisted research-task recovery after reload, self-improvement proposal validation/rejection, explicit confirmation for draft PR/rollback controls (GitHub-facing calls mocked), responsive navigation, skip-to-content, named controls, and overflow at 360/768/1280px. The responsive/accessibility implementation is in [PR #326](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/326), with post-merge accessible-name regression fixes in [PR #327](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/327). Manual screen-reader/keyboard review, broader operational controls, and target-machine usability validation remain. |
| Resource discovery | Read-only by policy | Local hardware detection and cloud resource discovery are available. Provisioning and remote execution intentionally remain disabled until provider authorization, budget limits, auditability, and security acceptance are specified and tested. |
| Governed self-improvement | Controlled proposal workflow implemented | Patch allowlists, isolated syntax and behavioral tests, test-integrity checks, explicit approval, draft PR creation, and draft rollback are available. Automatic merge, deployment, or production mutation remain disabled by design. |
| Security and release hardening | Partial; independent review required | A global loopback-only HTTP boundary blocks non-loopback peers before they reach the UI or API, with route-level guards retained as defense in depth. The target-host baseline now reports hostname-based Ollama endpoints as `unsafe_or_unverified` rather than assuming `localhost` resolves to loopback; it deliberately does not resolve DNS or probe the network. Issue #52 remains open for independent review of URL parsing, DNS/loopback behavior, proxy/redirect handling, validation, limits, and the broader network threat model. A reviewer independent of the implementer must record findings and retest remediations. |



The task dashboard includes a live summary and client-side search bounded to loaded pages; cursor pagination, durable status audit, interactive event history, status filtering, priority/schedule controls, and safe bulk operations are merged. semantic visual status styling with Persian labels are merged. Browser E2E verifies status classes, preserved raw identifiers, localized labels, and accessible names. This UI change does not alter API contracts or task behavior.

Task audit history was made interactively cursor-paginated in [PR #351](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/351), merged at `6f2137e6b60a0cf34361ec733b4b3d64171f1bd9`. Exact-head tests, browser E2E, and dependency audit passed ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324069), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324023), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324165)). Post-merge tests and browser E2E on `main` were still running when recorded; do not interpret PR CI as local or target-host acceptance.

## Required acceptance gates before calling the project complete

1. **Target-host baseline and legacy migration:** the reviewed exact-fingerprint, backup-first migration for the reported `learning.db` schema is merged in [PR #306](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/306); its fixture and data-preservation checks passed in CI. Read-only unknown-schema diagnostics and backup-first recovery instructions are now in [PR #327](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/327), including tests for non-mutation and no row-content disclosure. Run the complete test suite, `scripts/inspect_sqlite_schema.py` against the actual target databases, schema/storage health checks, and app startup on the actual Windows target after pulling `main`; verify the generated `learning.db.pre-migration-*.bak`, `/api/health`, and a second restart. Archive redacted outputs and exact commit/dependency versions. Windows-specific migration and rollback CI coverage is added by [PR #310](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/310); CI regression checks are green, but real-target migration/rollback acceptance remains open.
2. **Independent security review:** complete Issue #52, including threat model, reachable network boundaries, DNS/loopback behavior, request validation, and regression tests. Do not self-close this gate.
3. **Live-model quality evaluation:** run the versioned Persian/English evaluation dataset against the intended local model; capture model digest where available; obtain human review for factual correctness, source support, source conflicts, and prompt-injection resistance.
4. **Operational UI acceptance:** repository CI now includes real Chromium scenarios for research, task cancellation/recovery, consent revocation, experience review, backup preview/restore staging, self-improvement draft workflows, and responsive/accessibility smoke checks. The browser checks pass on the current main commit, but manual keyboard/screen-reader review and target-machine usability acceptance remain required.
5. **Recovery and load evidence:** run `scripts/benchmark_task_queue.py` on the target host using disposable SQLite stores and archive the JSON report; exercise process crash/restart and multi-process contention on disposable data; measure latency, memory, CPU, disk growth, queue recovery, and backup/restore behavior. The benchmark is diagnostic and has no universal throughput pass threshold.
6. **Deployment policy:** local install/update/rollback and backup procedures are documented in `docs/operations/INSTALL_UPDATE_ROLLBACK.md`; the target-host rehearsal, including staging restore and rollback, remains outstanding. Keep remote execution/cloud provisioning disabled until a separate security and cost review approves them.

## Additional release gate



7. **Final clean-start and test-data isolation:** all current learning content is disposable and must never be promoted as trusted release knowledge. After all implementation and evaluation gates pass, rehearse initialization using fresh disposable stores, verify the registered schema/migration history, confirm test learning content is absent, and rerun target-host smoke tests. Do not truncate or replace development/live stores to bypass schema errors. Follow [Final clean-start and learning-data acceptance](operations/FINAL_CLEAN_START_ACCEPTANCE.md). This gate remains outstanding until its evidence is attached to the exact release commit.

## Non-goals without a separate approval gate

- Do not auto-apply arbitrary generated patches to the running application.
- Do not auto-merge or deploy self-improvement PRs.
- Do not treat a model's own semantic review as independent verification.
- Do not enable cloud provisioning or remote execution by default.
- Do not replace live databases automatically during backup restoration.

## Evidence policy

Every acceptance claim must link to the exact commit and CI run or target-host report. A skipped test, mock, static check, or successful CI run must not be described as proof of a different environment or of factual model quality.


## Dashboard navigation refinement (2026-10-20)

The persistent section navigation now exposes exactly one current destination using `aria-current="location"`, updates its active item on scroll, resize, and hash changes with a requestAnimationFrame-throttled handler, and uses a responsive active indicator. Browser E2E asserts the initial state and navigation to the task manager. This is repository-level behavior only; manual keyboard/screen-reader and target-host usability acceptance remain required.


### Learning-path progress visualization (2026-10-20)

The learning dashboard derives a bounded progress percentage from completed and total topics and renders a native HTML progress bar with an accessible name, value text, and localized completed/total caption. The percentage is derived from counts rather than trusting a possibly inconsistent stored percentage. Responsive styling uses local CSS only. Exact-head browser CI passed and the feature is merged into current `main` ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788776), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368789105), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788829)); manual keyboard/screen-reader and target-host usability acceptance remain separate gates.


### Task dashboard empty states (2026-10-20)

The task queue now distinguishes a genuinely empty queue from zero matches caused by search/status filters. Filtered empty states expose a clear-filters action; text is created with textContent, no task payload is exposed, and the layout uses local responsive CSS. Browser E2E covers no-match recovery and the truly empty queue. Exact-head CI passed and the feature is merged into current `main` ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788776), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368789105), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788829)); manual accessibility and target-host usability remain separate gates.


### Dashboard theme preference (2026-10-20)

The dashboard theme preference is implemented and merged in [PR #365](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/365), commit `9702984f236e0c3fdefad16365e1127027e17b87`. Exact-head tests/Windows regressions, browser E2E, and dependency audit passed ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288911), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288901), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288902)). OS preference fallback and explicit local persistence are covered. Manual contrast, keyboard/screen-reader, and target-host acceptance remain open.
