# Seven-day repository completion plan

**Status:** Execution plan — active  
**Created:** 2026-10-30  
**Baseline:** `main` after PR #570, merge commit `aa4990ddd64b1060e5890d69509553a35d31bc7b`  
**Tracking issue:** [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290)

## 1. Objective and scope

Close as much as can be responsibly completed in the repository within seven consecutive work days, excluding:
- Local tests or validation that require the project owner's machine.
- Human review, live-model adjudication, and manual quality assessment.
- Independent security review by a reviewer other than the implementer.
- Target-host/staging/production operations that require the owner's environment or approval.

This is a time-boxed execution target, not permission to bypass quality gates. If a required CI workflow fails, a defect is unresolved, or evidence is insufficient, the affected item remains open rather than being marked complete.

## 2. Working rules

1. Work only from a fresh, verified `main` baseline.
2. Start with an audit of existing implementation, tests, documentation, and open issues; do not duplicate existing tests or features.
3. Every identified defect must have a focused regression test before or with its fix.
4. Keep each coherent workstream in a separate branch and pull request.
5. Required CI must pass on the exact PR head before merge. After merge, verify the resulting `main` SHA and its applicable CI.
6. Never commit directly to `main`, force-push, or merge a PR with pending/failed required checks.
7. Preserve user data. No destructive database operation, live migration, production reset, deployment, cloud provisioning, or remote execution.
8. Do not treat lexical benchmarks, model self-review, or automated structural checks as proof of factual quality or security.
9. Update the relevant documentation and this plan's execution log only with verified results.
10. If a workstream uncovers a larger architectural issue that cannot be safely closed within the time box, document the finding, risk, evidence, and next action instead of applying a broad speculative rewrite.

## 3. Seven-day sequence

### Day 1 — Baseline and UI audit
- Verify current `main`, open PRs/issues, and exact-head CI.
- Re-read the UI acceptance criteria and current browser/accessibility tests.
- Inventory navigation, semantic controls, keyboard/focus, contrast, RTL/LTR, responsive behavior, error/loading/empty/success states, and destructive-action confirmations.
- Produce a gap list tied to concrete files and missing tests.
- Avoid changing UI behavior until a non-duplicative gap is demonstrated.

**Exit evidence:** baseline SHA, CI status, audit findings, and a prioritized list of reproducible UI gaps.

### Day 2 — UI behavior and accessibility automation
- Add only missing browser or static regression coverage identified on Day 1.
- Fix confirmed semantic, keyboard/focus, responsive, RTL/LTR, state-feedback, or destructive-action defects.
- Check that new coverage does not duplicate existing tests.
- Update UI guidance and acceptance records.
- Open a focused PR; merge only after all required checks pass.

**Exit evidence:** regression tests, reviewed diff, green exact-head CI, post-merge verification, and documentation.

### Day 3 — API contracts and architecture
- Audit request/response validation, error boundaries, logging/redaction, settings/data ownership, router boundaries, and migration/runtime contracts.
- Inspect existing tests first and map each discovered defect to a missing regression case.
- Fix only bounded, evidence-backed defects; avoid large-scale module moves or formatting-only rewrites.
- Update architecture/API documentation and open a focused PR.

**Exit evidence:** defect-to-test mapping, green exact-head CI, post-merge verification, and documented residual risks.

### Day 4 — Learning quality and provenance
- Audit language adherence guards, source capture/provenance, source conflict handling, uncertainty behavior, prompt-injection boundaries, and user-feedback trust transitions.
- Verify existing safeguards and benchmarks before adding coverage.
- Add deterministic tests for gaps that can be proven without a live model or human judgement.
- Correct confirmed defects and update learning/provenance documentation.

**Exit evidence:** deterministic regression coverage and CI evidence. Live answer quality and human adjudication remain explicitly pending.

### Day 5 — Persistent learning and feedback governance
- Audit feedback capture, review/verification states, persistence, later-run retrieval, provenance/version history, and rollback paths.
- Verify that untrusted feedback cannot silently become trusted knowledge.
- Distinguish persistent memory/RAG from model-weight training in code, tests, and documentation.
- Add fault-injection or database regression tests for uncovered deterministic failure modes.

**Exit evidence:** tested state transitions and persistence/retrieval invariants, green CI, and documented limitations.

### Day 6 — Resilience, storage, and recovery
- Audit task crash/restart, retry/cancel/recovery, multi-process coordination, bounded resource use, backup verification, isolated restore, and rollback logic.
- Reuse existing Windows/storage/browser tests; add only missing deterministic fault-injection and isolation tests.
- Ensure tests use disposable stores and never mutate real user databases.
- Fix confirmed defects in focused PRs and update operations documentation.

**Exit evidence:** exact-head CI including applicable Windows/storage/browser workflows; target-host soak and staging rehearsal remain external gates.

### Day 7 — Reconciliation and release-gate report
- Reconcile `PROJECT_STATUS.md`, `ROADMAP_ACCEPTANCE.md`, `INTEGRATION_REPORT.md`, and this plan against merged commits and actual CI evidence.
- Verify every claimed completion maps to implementation, automated test, documentation, and required evidence.
- List all remaining blocked, owner-only, human, or independent-review gates explicitly.
- Verify final `main` SHA and all applicable post-merge workflows.
- Publish a concise gate-by-gate status; do not declare the project 100% complete while any required gate is open.

**Exit evidence:** consistent documentation, final verified SHA, CI run links, and a truthful list of remaining gates.

## 4. Definition of done for a repository work item

A work item is complete only when all applicable conditions are met:
- The requirement and affected behavior are understood from the current code and docs.
- The change is minimal, reviewed, and isolated in a focused PR.
- A regression test fails before the fix (where practical) and passes after it.
- Relevant full-suite, browser, Windows/storage, dependency, and other required CI checks pass on the exact PR head.
- The PR is merged without bypassing branch/CI policy.
- Applicable post-merge checks pass on the resulting `main` commit.
- Documentation and acceptance ledger reflect the verified behavior and its limitations.

If an item requires a human, live model, target host, or independent reviewer, it must be marked **blocked/external**, not complete.

## 5. Out-of-scope acceptance gates (remain visible)

The following are deliberately not represented as completed by this seven-day repository effort:
- Live Persian/English model quality evaluation and human adjudication.
- Owner-machine/local validation.
- Target Windows host baseline, long-run load/soak, and operational recovery evidence.
- Operator-run staging backup/restore/rollback and clean-start rehearsal.
- Independent security review for the Ollama loopback boundary (Issue #52).
- Any deployment, production mutation, remote execution, or cloud provisioning requiring separate approval.

## 6. Prioritization and contingency

Priority order when time is constrained:
1. Data safety, security boundaries, and correctness defects.
2. Task/learning persistence and recovery defects.
3. API contract and cross-module regression risks.
4. User-visible UI/accessibility defects.
5. Documentation reconciliation and non-blocking cleanup.

Do not reduce test scope, skip review, or merge with failing/pending checks to meet the date. If CI latency, external dependencies, or a high-risk defect makes the seven-day target infeasible, record the reason and continue safely; the deadline never overrides correctness.

## 7. Execution log

Append dated entries with:
- Date and baseline/head/merge SHA.
- Audit scope and findings.
- PR number and changed behavior.
- Exact CI run IDs and conclusions.
- Post-merge SHA and CI conclusions.
- Remaining risks, blocked gates, and next action.

### 2026-10-30
- PR #570 documentation reconciliation merged after all three exact-head workflows (tests, browser E2E, dependency audit) completed successfully.
- Merge commit: `aa4990ddd64b1060e5890d69509553a35d31bc7b`.
- Seven-day execution plan created from this verified baseline. No claim is made that the remaining workstreams are already complete.


### 2026-10-30 — Day 1 UI audit
- Verified the post-PR #571 `main` baseline: `f403ae13a03a9ac5e526879d40380221c879cff8`. Post-merge workflows were triggered; at audit time, `tests` and `browser-e2e` were in progress and `dependency-audit` had succeeded.
- Inventory: 14 top-level dashboard navigation sections, 2 nested subpanels (experience auto-capture and programming assistance), 1 storage-degraded banner, 56 static buttons, and 27 independent browser E2E tests.
- The navigation/layout browser test checks all 14 top-level links and section nodes and verifies their HTTP routes return 200. Dedicated workflow-interaction tests explicitly open 7 sections: storage health, learning paths, experience memory, cloud resources, task manager, self-improvement, and learning.
- The remaining top-level sections (knowledge, cloud plan, Kaggle quota, OCI limits, sandbox, and settings) have route-presence checks but no dedicated browser workflow-interaction test in `tests/test_browser_e2e.py`. The two nested subpanels likewise lack independent route-level coverage because they are not top-level pages. This is a test-coverage gap, not evidence that those features are broken; each workflow needs a code/API-to-test mapping before deciding whether new tests are warranted.
- Corrected documentation mismatches in `docs/UI_ACCEPTANCE.md`: the navigation implementation and browser assertion use `aria-current=page`, not `aria-current=location`; current-section selection is route-based, not scroll/resize/hash-driven. Also reconciled stale statements in `docs/architecture/SANDBOX_EXECUTION.md` (the consent-gated loopback submission API already exists) and `docs/architecture/CLOUD_RESOURCE_SELECTION.md` (the loopback dry-run planning API already exists).
- Existing semantic-button, theme contrast, RTL/LTR, responsive viewport, task-state, confirmation, and storage-failure checks were reviewed; no duplicate tests were added.
- Remaining Day 1 work: map the seven uncovered sections to their actual forms/actions/API contracts, inspect their loading/error/empty/success states and confirmation requirements, then decide which omissions represent meaningful regression risk. Do not mark broader functional parity complete based on route coverage alone.
