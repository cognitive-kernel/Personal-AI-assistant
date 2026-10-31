# Engineering Delivery Estimate — 2026-11-01

## Executive commitment

- **Planning baseline:** 22 working days.
- **Capacity assumption:** one full-time engineering stream, 8 engineering hours per working day, with no waiting for owner-run local tests or human adjudication.
- **Budget:** 176 engineering hours total = 144 estimated task hours + 32 hours contingency.
- **Target date:** 2026-12-01, assuming work starts on 2026-11-02 and a Monday–Friday calendar with no holiday adjustment.
- **Baseline commit:** `348be1d2bd9f69e91f9631911f889e8ab6f442d2` (main after PR #672).
- **Status:** planning estimate, not a claim that a complete line-by-line audit has already been performed.

The arithmetic is exact; the forecast is not a guarantee. A precise estimate cannot be derived from the previous 60.47% gate score because that score measures evidence stages, not remaining engineering hours. This estimate therefore uses explicit work packages and a separately visible contingency. It must be revised only through the change-control rules below.

## Repository baseline inspected

At the baseline commit, the Git tree contains 412 tracked files: 285 Python files, 157 Python test files, and 83 Markdown files under `docs/`. The repository already has substantial automated coverage and CI workflows. The open engineering checklist in `docs/PROJECT_STATUS.md` still calls out a complete repository/module/data-ownership audit, broader dynamic-boundary review, and automated fault-injection coverage for all task types. Issue #290 additionally requires feature-to-test mapping, API/architecture contracts, AI-learning quality controls, UI functional parity, resilience and release-gate documentation.

This is a GitHub tree/documentation inventory, not a completed checkout-wide line-by-line audit. The task-hour estimate includes doing that audit.

## Work-package estimate

| ID | Engineering work package | Concrete completion output | Estimate |
|---|---|---|---:|
| E-01 | Full repository and architecture audit | File/module inventory; data ownership and runtime dependency map; duplicate/dead-code candidates verified; risk-ranked findings with file/line references; no speculative deletion | 20 h |
| E-02 | Architecture, API and persistence contracts | Endpoint-by-endpoint contract review; validation/error/timeout/cancellation semantics; database ownership, migrations, transaction and compatibility findings; regression fixes | 18 h |
| E-03 | AI, research and learning engineering | Trace research/source provenance and failure paths; address reproducible code defects in source capture, language controls, conflict/uncertainty handling and feedback governance; automated regressions | 22 h |
| E-04 | UI functional parity and accessibility engineering | Map each required workflow to browser tests; close missing/error/loading/empty states and functional gaps; automated RTL/LTR, responsive, keyboard/focus and destructive-action coverage | 22 h |
| E-05 | Durable tasks and resilience | Build a task-type fault-injection matrix; cover pause/cancel/retry/restart/lease/worker failure and recovery boundaries; fix defects and add automated integration regressions | 18 h |
| E-06 | Security implementation hardening | Code-level threat-path review and automated tests for URL/network boundaries, data exposure, validation, headers, permissions and redaction; implement and retest code remediations | 12 h |
| E-07 | Performance, dependency and CI engineering | Inspect measured bottlenecks and resource bounds; dependency/supply-chain cleanup where justified; close required automated CI gaps and flaky tests | 8 h |
| E-08 | Integration, final engineering review and documentation | Cross-workstream regression pass; diff/contract review; feature-to-test matrix; update architecture/API/operations/roadmap/engineering records; exact-SHA CI evidence | 24 h |
|  | **Task estimate subtotal** |  | **144 h** |
| E-09 | Explicit contingency reserve | Uncovered defects, integration rework, CI failures and estimate error discovered during E-01…E-08. Reserve is not pre-spent scope. | **32 h** |
|  | **Total planned engineering budget** | 144 h + 32 h = 176 h; 176 h / 8 h per day | **22 working days** |

The contingency is 22.2% of the 144-hour task estimate. It is included in the delivery commitment, not added on top of the 22 days.

## Included in this deadline

- Source-code, architecture, API, persistence and runtime engineering.
- Automated unit, integration, browser E2E, fault-injection, security-regression and CI work.
- Fixes discovered by those automated checks.
- Code-level security remediation arising from an independent review, if findings are supplied during the work.
- Performance/reliability changes supported by measurements or reproducible defects.
- Technical documentation and evidence needed to establish engineering completion.
- Focused PRs, reviews, merges and exact-commit CI verification.

## Explicitly excluded

These do not consume the 22-day engineering budget and do not lower the engineering-completion percentage:

- Owner-run local installation, local manual testing and local acceptance.
- Human review/adjudication of live Persian/English model answers.
- Manual screen-reader and human usability evaluation.
- Target-host operator baseline, physical-device/host soak, and manual operational rehearsal.
- Independent security review itself (Issue #52). Security fixes in source code resulting from its findings are engineering work and must be estimated/recorded separately if they exceed the 32-hour reserve.

Automated simulations and CI coverage for these areas remain included. Excluding an operator-run rehearsal does not exclude writing/fixing the code or automated tests that make the workflow reliable.

## Definition of 100% engineering completion

Engineering completion is reached only when all of the following are true on the final merged `main` SHA:

1. Every in-scope feature in Issue #290 is mapped to implementation plus an automated test, or explicitly documented as intentionally disabled/non-goal.
2. The repository and architecture audit is complete with file/line evidence and all accepted high/critical engineering findings fixed or explicitly scoped.
3. API, data ownership, migration and runtime contracts are reviewed; required code fixes and regression tests are merged.
4. Required AI/research/learning and feedback-governance code paths have automated regressions for the identified engineering requirements.
5. Required UI workflows and error states have automated browser coverage, including RTL/LTR and keyboard/focus behavior.
6. Task lifecycle and recovery fault-injection coverage is complete for every supported task type.
7. Required security implementation checks and dependency audits pass; known code-level findings are fixed or explicitly documented with rationale.
8. Required CI workflows pass on the exact final commit; no required automated suite is silently deselected or skipped.
9. Technical documentation, roadmap, scorecard and engineering log match the implementation and exact-SHA evidence.

This is **100% engineering completion**, not overall product/release acceptance. External human, local-host and independent-review gates remain separately visible and must not be represented as passed.

## Schedule and forecast controls

- One working day = 8 engineering hours.
- Baseline start: 2026-11-02.
- Baseline delivery: 2026-12-01 (22 weekdays; public holidays are not subtracted).
- The 32-hour reserve absorbs ordinary uncertainty. Work is not declared complete merely because the date arrives.
- At each merged work package, record planned hours, actual engineering hours, merged SHA, CI run IDs, remaining hours and revised finish date in `docs/ENGINEERING_LOG.md`.
- Remaining-days formula: `ceil(remaining approved engineering hours / 8)`.
- A forecast change requires a dated entry naming the newly discovered task, evidence, added hours, whether contingency was consumed, impact on the finish date, and the PR/issue that introduced the change.
- If a task is blocked by an external prerequisite excluded above, record the blocker and its owner separately; do not silently charge human waiting time to engineering.
- If new scope is requested, estimate and approve it separately before adding it to the baseline.
- Do not rewrite the original baseline when the forecast changes. Preserve the original 22-day commitment and append each reforecast so a later audit can explain what changed and why.

## Why the previous 12-day estimate is not the baseline

The earlier 12-day figure had no task-level hours, explicit contingency, file-level audit evidence, or change-control record. It is therefore withdrawn and retained only as a historical, unsupported estimate. It must not be used to judge delivery performance. This 22-day baseline is the first estimate in this project record with an explicit work-package calculation; its accuracy will be assessed against actual logged engineering hours.

## Historical progress-score note

The older `docs/ACCEPTANCE_SCORECARD.md` reports 60.47% engineering progress using weighted gate-stage factors. That figure is preserved as a historical evidence-stage score; it is not converted into hours and is not used as the denominator for this delivery estimate. A granular engineering ledger must supersede it only when each work package has auditable status and evidence.
