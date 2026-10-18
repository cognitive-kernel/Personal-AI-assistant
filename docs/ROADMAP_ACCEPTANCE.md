# Roadmap acceptance status

Last reviewed: 2026-10-17

This document records repository-level evidence and the remaining acceptance gates. A merged implementation or passing CI suite does not by itself establish production readiness on a user's machine.

## Current status

| Stage | Status | Evidence and remaining gate |
|---|---|---|
| Repository audit and baseline | Partial | A redacted target-host baseline tool now records commit/runtime, read-only SQLite health, and optional full pytest counts. The tool still must be run on the actual target host; complete checkout and broader supply-chain audit remain; a scheduled `pip-audit` workflow now checks the resolved Python environment on pull requests, main pushes, and weekly. |
| Module boundaries and contracts | Partial | The merged AST-based audit (`scripts/check_import_cycles.py`) scans 55 Python modules and found no eager module-time import cycles on its checked head. Dynamic/runtime dependencies and architecture-level refactoring still require broader review. |
| Persistent task execution | Implemented with bounded evidence | SQLite queue, leases, checkpoints, cancellation, recovery, thread-race tests, a six-process independent-connection claim test, an abrupt worker-process termination/checkpoint-recovery test, and an isolated concurrent-enqueue/claim-complete benchmark are covered by CI. The benchmark now rejects paths aliasing configured application SQLite stores and rejects identical settings/learning paths before initialization. Run `python scripts/benchmark_task_queue.py --tasks 1000 --producers 4 --workers 4 --output reports/task-queue-benchmark.json` on the target host and archive the report. Target-host crash/restart, storage fault injection, sustained load, and recovery across every task type remain. |
| Research and learning | Implemented with limitations | Source-backed research, provenance, source-conflict reporting, local quality diagnostics, learning paths, recommendations, spaced review, and advisory semantic review exist. Live Ollama runs and human adjudication on a versioned evaluation set remain necessary before claiming knowledge quality. |
| Isolated execution | CI-validated | Docker sandbox and self-improvement test runner use restricted containers; CI includes real Docker integration with a pinned image. Target-machine installation and operational validation remain. |
| Experience memory | Implemented with limitations | Versioned experiences, explicit capture consent, source checks, freshness-aware hybrid retrieval, explicit feedback, and controlled verification exist. Retrieval relevance still needs a broader adjudicated evaluation set. |
| Local operations UI | Partial | Chat/task controls, learning, experience review, storage health, backup/restore staging, and self-improvement controls exist. Real Chromium CI covers dashboard loading, storage health, recommendations, experience creation, consent revocation after reload, experience review persistence without implicit verification, backup-to-staging-restore, cancellation of a persisted scheduled task, persisted research-task recovery after reload, self-improvement proposal validation/rejection, and explicit confirmation for draft PR/rollback controls (GitHub-facing calls mocked). Broader operational controls and target-machine usability validation remain. |
| Resource discovery | Read-only by policy | Local hardware detection and cloud resource discovery are available. Provisioning and remote execution intentionally remain disabled until provider authorization, budget limits, auditability, and security acceptance are specified and tested. |
| Governed self-improvement | Controlled proposal workflow implemented | Patch allowlists, isolated syntax and behavioral tests, test-integrity checks, explicit approval, draft PR creation, and draft rollback are available. Automatic merge, deployment, or production mutation remain disabled by design. |
| Security and release hardening | Partial; independent review required | A global loopback-only HTTP boundary blocks non-loopback peers before they reach the UI or API, with route-level guards retained as defense in depth. The target-host baseline now reports hostname-based Ollama endpoints as `unsafe_or_unverified` rather than assuming `localhost` resolves to loopback; it deliberately does not resolve DNS or probe the network. Issue #52 remains open for independent review of URL parsing, DNS/loopback behavior, proxy/redirect handling, validation, limits, and the broader network threat model. A reviewer independent of the implementer must record findings and retest remediations. |

## Required acceptance gates before calling the project complete

1. **Target-host baseline:** run the complete test suite, schema/storage health checks, and app startup on a clean target-machine checkout; archive redacted outputs and exact commit/dependency versions.
2. **Independent security review:** complete Issue #52, including threat model, reachable network boundaries, DNS/loopback behavior, request validation, and regression tests. Do not self-close this gate.
3. **Live-model quality evaluation:** run the versioned Persian/English evaluation dataset against the intended local model; capture model digest where available; obtain human review for factual correctness, source support, source conflicts, and prompt-injection resistance.
4. **Operational UI acceptance:** run browser-level end-to-end scenarios for research, task cancellation/recovery, consent revocation, experience review, backup preview/restore staging, and self-improvement draft workflows.
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
