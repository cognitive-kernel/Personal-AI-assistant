# Roadmap acceptance status

Last reviewed: 2026-10-16

This document records repository-level evidence and the remaining acceptance gates. A merged implementation or passing CI suite does not by itself establish production readiness on a user's machine.

## Current status

| Stage | Status | Evidence and remaining gate |
|---|---|---|
| Repository audit and baseline | Partial | GitHub source and CI history have been reviewed. A complete checkout audit, dependency/supply-chain review, and recorded baseline run on the target host are still required. |
| Module boundaries and contracts | Partial | Functional modules and route boundaries exist. A full dependency-cycle review and architecture-level refactor have not been accepted as complete. |
| Persistent task execution | Implemented with bounded evidence | SQLite queue, leases, checkpoints, cancellation, recovery, and race-condition tests exist. Fault injection across every task type and real multi-process/crash testing remain. |
| Research and learning | Implemented with limitations | Source-backed research, provenance, source-conflict reporting, local quality diagnostics, learning paths, recommendations, spaced review, and advisory semantic review exist. Live Ollama runs and human adjudication on a versioned evaluation set remain necessary before claiming knowledge quality. |
| Isolated execution | CI-validated | Docker sandbox and self-improvement test runner use restricted containers; CI includes real Docker integration with a pinned image. Target-machine installation and operational validation remain. |
| Experience memory | Implemented with limitations | Versioned experiences, explicit capture consent, source checks, freshness-aware hybrid retrieval, explicit feedback, and controlled verification exist. Retrieval relevance still needs a broader adjudicated evaluation set. |
| Local operations UI | Partial | Chat/task controls, learning, experience review, storage health, backup/restore staging, and self-improvement controls exist. Full browser end-to-end testing and usability validation on the target machine remain. |
| Resource discovery | Read-only by policy | Local hardware detection and cloud resource discovery are available. Provisioning and remote execution intentionally remain disabled until provider authorization, budget limits, auditability, and security acceptance are specified and tested. |
| Governed self-improvement | Controlled proposal workflow implemented | Patch allowlists, isolated syntax and behavioral tests, test-integrity checks, explicit approval, draft PR creation, and draft rollback are available. Automatic merge, deployment, or production mutation remain disabled by design. |
| Security and release hardening | Partial; independent review required | Loopback boundaries and several fault-injection/security tests exist. Issue #52 remains open for an independent review of the Ollama boundary. A reviewer independent of the implementer must record findings and retest remediations. |

## Required acceptance gates before calling the project complete

1. **Target-host baseline:** run the complete test suite, schema/storage health checks, and app startup on a clean target-machine checkout; archive redacted outputs and exact commit/dependency versions.
2. **Independent security review:** complete Issue #52, including threat model, reachable network boundaries, DNS/loopback behavior, request validation, and regression tests. Do not self-close this gate.
3. **Live-model quality evaluation:** run the versioned Persian/English evaluation dataset against the intended local model; capture model digest where available; obtain human review for factual correctness, source support, source conflicts, and prompt-injection resistance.
4. **Operational UI acceptance:** run browser-level end-to-end scenarios for research, task cancellation/recovery, consent revocation, experience review, backup preview/restore staging, and self-improvement draft workflows.
5. **Recovery and load evidence:** exercise process crash/restart and multi-process contention on disposable data; measure latency, memory, CPU, disk growth, queue recovery, and backup/restore behavior.
6. **Deployment policy:** document local install/update/rollback and backup procedures. Keep remote execution/cloud provisioning disabled until a separate security and cost review approves them.

## Non-goals without a separate approval gate

- Do not auto-apply arbitrary generated patches to the running application.
- Do not auto-merge or deploy self-improvement PRs.
- Do not treat a model's own semantic review as independent verification.
- Do not enable cloud provisioning or remote execution by default.
- Do not replace live databases automatically during backup restoration.

## Evidence policy

Every acceptance claim must link to the exact commit and CI run or target-host report. A skipped test, mock, static check, or successful CI run must not be described as proof of a different environment or of factual model quality.
