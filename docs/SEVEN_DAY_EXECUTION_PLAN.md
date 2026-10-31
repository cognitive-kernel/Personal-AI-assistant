# Seven-day repository completion plan

**Status:** Execution plan — active  
**Created:** 2026-10-30  
**Baseline:** `main` after PR #634, merge commit `b50daaaaa12fab13d6526a47335613bd30a63eba`
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
- Verified baseline `main` SHA: `f403ae13a03a9ac5e526879d40380221c879cff8`; PR #571 post-merge workflows all passed: tests [#41010845397](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41010845397), browser E2E [#41010845510](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41010845510), dependency audit [#41010845531](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41010845531).
- Audited `index.html`, `page-navigation.js`, `test_browser_e2e.py`, `test_ui_semantics.py`, `test_ui_contrast.py`, and `UI_ACCEPTANCE.md`.
- Confirmed 14 primary navigation destinations are checked for link/section presence and HTTP 200 in the existing navigation browser test; 27 browser test functions and 56 static button controls exist.
- Corrected stale acceptance wording: implementation uses `aria-current="page"`; it does not implement scroll-position `aria-current="location"` behavior.
- Added `docs/UI_AUDIT_2026-10-30.md` with verified coverage, limits, and candidates for Day 2. The untested cloud planning/quota, OCI, sandbox, programming-assistance, knowledge, and non-theme settings interactions are audit candidates, not confirmed defects.
- No application behavior changed and no new tests were added because the first pass found no reproduced UI defect; duplicate navigation coverage would add no value.
- Next: map candidate workflows to UI handlers, API contracts, backend tests, and missing state transitions before selecting a regression test.

### 2026-10-30 — Day 2 browser coverage: sandbox
- PR #574 added `test_browser_sandbox_consent_fails_closed_and_runs_bounded_task`; Chromium verified failed status lookup remains fail-closed, explicit consent gating, bounded request payload, task completion rendering, and disablement after revocation.
- Exact PR-head CI passed: tests [#41018483105](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483105), browser E2E [#41018483069](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483069), dependency audit [#41018483133](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483133). Windows storage regressions and repository artifact/link/import/retrieval/claim-to-test checks also passed within the test workflow.
- PR #574 merged as `968b10bc7d731f7a241fbc9174d2837d994ed514`. Independent post-merge CI completed successfully: tests, browser E2E, dependency audit, and Windows storage regressions passed.
- This is a mocked browser/API test, not proof of Docker execution, target-host sandbox isolation, or manual acceptance. The test suite now contains 28 browser test functions.
- Next: audit cloud planning dry-run and its stale-evidence/error presentation; preserve the no-execution authorization boundary.


### 2026-10-30 — Architecture documentation parity follow-up
- Reviewed the stale Day 1 documentation-only PR #572 against the current `main` implementation and later documentation changes. Its navigation acceptance correction is already present; its old branch is divergent and is not merged to avoid reintroducing stale copies of shared files.
- Reconciled the remaining cloud and sandbox architecture statements: the loopback cloud dry-run endpoint and consent-aware sandbox submission routes already exist. Documentation now distinguishes those local APIs from provider execution, Docker runtime acceptance, and independent security review.
- No runtime code or test behavior changed in this documentation-only follow-up.


### 2026-10-30 — Day 2 continuation: API settings and browser persistence
- PR #577 یکسان‌سازی قرارداد URL loopback بین Settings API و Ollama runtime را با تست malformed authority/port و atomic rejection تکمیل کرد؛ ادغام `c79e2a008cb722930d2bfe979ceaadc18637318c` و post-merge CI کامل موفق شد.
- ممیزی واقعی فرم Settings در Chromium نقصی را آشکار کرد: سه setting مورد استفادهٔ UI در `SETTING_DEFAULTS` seed نشده بودند. PR #579 defaultهای `learning_model_cost_profiles`، `learning_model_minimum_quality_score` و `learning_task_budget_microusd` را اضافه کرد و API برای object، quality score محدود به 0..1 و بودجهٔ nullable safe-integer اعتبارسنجی شد. تست browser ذخیرهٔ موفق، مدل دستی، خطای 503 و عدم تغییر دادهٔ پایدار را پوشش می‌دهد.
- PR #579 در `cc1ad50536e39b44027c61c903873d8638da1df5` ادغام شد؛ exact post-merge CI: tests [#41031477950](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41031477950) (1488 passed, 5 skipped)، browser E2E [#41031478076](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41031478076)، dependency audit [#41031478004](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41031478004) و Windows storage regressions — همگی موفق.
- شمار آزمون‌های تابعی Browser E2E اکنون 31 است. آزمون‌های جدید فقط رفتارهای فاقد پوشش را اضافه می‌کنند؛ navigation و مسیرهای موجود دوباره‌کاری نشده‌اند.
- انتخاب مدل یادگیری به مسیر واقعی تولید درس در PR #582 متصل و در main ادغام شد؛ regressionها انتخاب مدل واجد شرایط، رد fail-closed، خطای discovery، fallback بدون محدودیت و عدم مصرف بودجه هنگام رد مدل را پوشش می‌دهند.
- race در cleanup کانتینرهای Sandbox در PR #581 بازتولید و اصلاح شد؛ تست هم‌زمانی و CI با Docker واقعی موفق شدند.
- PR #584 برای بستن این شکاف پیاده‌سازی شده است: claim review اختیاری منابع وب در برآورد هزینه وارد می‌شود، همان مدل انتخاب‌شده را استفاده می‌کند و پیش از اجرا در بودجهٔ durable رزرو می‌شود. تا پایان CI روی head دقیق و ادغام، این قابلیت هنوز بخشی از `main` نیست.


### 2026-10-30 — Sandbox cleanup and runtime learning model selection
- PR #581: concurrent orphan-container cleanup now verifies absence after a failed removal before reporting an idempotent skip; persistent failure and verification errors remain visible. Exact post-merge tests, browser E2E, dependency audit, and real-Docker Sandbox integration passed on `483343a2d9d67b6c8f7e5133497c8476440cfa5e`.
- PR #582: connected model cost/quality selection to `learning.run_job`, enforced hard configured constraints before inference, preserved backward-compatible fallback only when no hard constraint exists, and recorded selection estimates in provenance. Exact PR-head CI passed on `55c22bba29ea7174b211202a03458d44ea9af287`; post-merge CI passed on `7ccba9de397f6de63c2535d2d2e955af245e7da1` (1494 passed, 5 skipped, browser E2E, dependency audit, Windows storage).
- The initial PR #582 test run found one provenance-field contract mismatch; it was fixed and the full suite rerun successfully. No failing workflow was bypassed.
- PR [#584](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/584) implements the optional web-source claim-review model call in cost selection and generation provenance; merged as `20e30eeb77bf04e2169ced9db32ba1cff036c3d6`. Post-merge tests, dashboard smoke, Windows storage regressions, and dependency audit all passed. External gates remain Issue #52 independent security review, target-host/soak, live Ollama quality, human adjudication, and operator staging/rollback.


### 2026-10-30 — unified model selection and claim-review budget
- Claim review now uses the model selected for the learning job; the selector estimates its bounded prompt and 1,000-token completion only when usable web excerpts exist.
- The durable per-job/global budget reserves the claim-review stage once and refuses duplicate/exhausted calls. Provider usage counters are retained in claim-review provenance.
- Exact-head CI and post-merge evidence are required before this item is marked complete.


### 2026-10-31 — cloud-resource stale-evidence API regression and baseline reconciliation

- Reconciled the repository baseline after PR #611: `main` is `2fd885a583e0c6252aa6b77a23c5cff56bc87c31`; no open PRs were returned by the repository query at this checkpoint.
- PR [#611](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/611) added an API regression for a previously approved/available cloud candidate whose last observation is 31 days old. It independently exercises stale claim review and candidate review, expects HTTP 409 for both, and verifies the list API exposes stale freshness with availability reset to unknown. The change is test-only; production behavior and schema are unchanged. Exact PR-head CI on `ec693cb218b7f311c7ed82458d67ada0f58cdf4a` passed: tests [#41158610985](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610985) (including Windows storage regressions and repository quality checks), browser E2E [#41158611003](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158611003), and dependency audit [#41158610974](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610974). Squash merge commit: `2fd885a583e0c6252aa6b77a23c5cff56bc87c31`. The available GitHub evidence did not confirm post-merge workflows on the squash SHA, so post-merge CI is not claimed. Automated Codex review could not complete because the repository review quota was exhausted; no completed independent code review is claimed.
- Reviewed the relevant service/API path in `app/cloud_resources.py` and `app/cloud_resource_routes.py`: stale checks are applied before claim/candidate review mutations, and HTTP routes map the conflict to 409. The new regression verifies both review endpoints and the list projection; it does not change production code.
- Updated `PROJECT_STATUS.md`, `INTEGRATION_REPORT.md`, and `ROADMAP_ACCEPTANCE.md` to record the current baseline and distinguish exact-head CI from unconfirmed post-merge CI.
- Next repository workstream: Day 3 API/architecture contract audit, beginning with router ownership, request validation, error mapping, settings/data ownership, SQLite transaction boundaries, and migration/runtime contracts. Preserve the existing module boundaries; add only non-duplicative regression coverage for confirmed defects.

### 2026-10-31 — source-discovery audit diagnostics and browser inventory reconciliation

- PR #617 added bounded provider/fetch diagnostics to the no-source research audit and answer-quality report. The persisted shape is allowlisted and excludes failed page URLs, query text, page content, and raw exception messages.
- Exact-head CI on `8c6aa6d68bafc95ee2509b59b9db786271e43c70` passed: tests 1722 passed/5 skipped (including Windows storage regressions), browser E2E, dependency audit, generated-artifact validation, 77-file Markdown links, import-cycle scan, retrieval benchmark, and 10-case claim-to-test benchmark. PR #617 merged as `e6c40e2362d1082fb9631009a693db61cfc7fffc`; post-merge CI on that squash commit also passed: tests [#41162449390](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162449390) (1722 passed/5 skipped), Windows storage, browser smoke [#41162449395](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162449395), and dependency audit [#41162449391](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162449391).
- Recounted \`tests/test_browser_e2e.py\`: 41 independent functions. The suite already includes AI-learning topic approval/lesson review/publish/reload and Sandbox cancellation retry/settlement. Updated the UI audit to remove these stale missing-coverage claims while retaining the fact that these browser tests stub APIs.
- Next software work: review persistent learning retrieval/version/rollback, UI RTL/LTR behavior and accessible interaction, and the local browser-origin/CSRF boundary. The Linux second-stage payload integrity implementation is now complete and documented; live target-host installation remains an external gate. Do not re-add duplicate browser coverage. Human adjudication, local/target-host tests, staging rehearsal, and independent security review remain separate external gates.

### 2026-10-31 — provenance and worker-ownership documentation reconciliation

- Audited the current `main` baseline `3218334930bb75a823804945e72b52279edcb6f3`, open PRs, current CI, and the AI-learning/claim-review implementation against the status documents.
- PR [#623](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/623) was merged as `1e46eff5aeafd1a470a421df6dbea81629a89266`. Its exact-head tests and all four post-merge workflows (tests, Windows storage regressions, dashboard smoke, dependency audit) passed.
- Found stale statements in `docs/AI_LEARNING_STATUS.md` claiming that `app/ai_learning/` had no cross-process lease. This contradicted merged PR #604 and the current implementation/engineering document. The status now records the SQLite owner lease, fencing epoch, 90-second expiry, 15-second heartbeat, single-host scope, and remaining target-host acceptance.
- Found stale claim-review documentation describing prompt version v1 and no durable chat audit. It now records v2 and the bounded privacy-preserving audit fields while explicitly retaining the non-persistence of raw prompts, answers, claims, reasons, and source excerpts.
- PR #622's first exact-head test run exposed five fixture failures caused by tests still mocking the former URL-validation function. The implementation's HTTPS/DNS validation was not weakened; nine test fixtures were updated to mock the current parse/address-validation boundary. Security review then found and closed an HTTPS-to-HTTP redirect downgrade path, with negative and positive redirect regressions. Final exact-head CI passed (1735 passed/5 skipped); PR #622 merged as `c46e136bb00e4dd56627e98990b80d27f73b93e9`, and all post-merge workflows passed.
- Remaining external gates are unchanged: live-model/human adjudication, independent security review (Issue #52), owner-machine/target-host validation, staging restore/rollback, and load/soak acceptance.

### 2026-10-31 — HTTPS research policy, local Host boundary, and final documentation reconciliation

- PR #622 merged the HTTPS-only research source contract and HTTPS-to-HTTP downgrade redirect rejection as `c46e136bb00e4dd56627e98990b80d27f73b93e9`. Exact-head CI: 1735 passed/5 skipped; post-merge tests, Windows storage, dashboard smoke, and dependency audit all passed.
- PR #623 merged durable, bounded claim-review provenance in the chat audit as `1e46eff5aeafd1a470a421df6dbea81629a89266`. The audit stores hashes and bounded metadata/counters, not raw prompts, answers, claims, review reasons, or source excerpts. Exact-head and post-merge CI passed.
- PR #624 reconciled stale AI-learning worker-lease and claim-review documentation as `acb821df03c8c24c7b18cc912f3a1881964fb863`; exact-head tests (1730 passed/5 skipped) and all post-merge workflows passed.
- PR #625 fixed the asynchronous browser-test assertion race for self-improvement proposal rendering as `04d325e244fc5b231d74ac7b700b571b4cf45283`. The UI success message precedes list refresh; the test now waits for the matching card. Exact-head and post-merge CI passed; application behavior did not change.
- PR #626 added Host-header validation alongside loopback peer validation to mitigate DNS rebinding against the local dashboard, merged as `6fdd176e81aa408ff17cc1c01ba66ec198f42240`. Post-merge CI: 1737 passed/5 skipped; Windows storage, dashboard smoke, and dependency audit passed. The control is not authentication or a complete CSRF defense.
- PR #627 corrected the stale Linux Ollama installer security document to reflect the verified second-stage archive from PR #620, merged as `7a2cf05a7b3485e3b157e41860783fda2e76a846`. Exact-head CI passed (1730 passed/5 skipped); post-merge CI on current `main` passed (1737 passed/5 skipped in 176.88s), including Windows storage, dashboard smoke, and dependency audit.
- Reconciled the current repository snapshot: 400 tracked files, 285 Python files, 157 Python test files, 71 Markdown documents under `docs/`, and 41 independent Chromium E2E test functions. This inventory is not a claim of a line-by-line review of every file.
- Still open: live-model and human adjudication; independent security review (Issue #52); target-host install/startup/crash/soak; real-Docker integration/target-host acceptance; staging restore/rollback; persistent learning retrieval/version/rollback proof; and the remaining UI RTL/LTR, screen-reader, contrast, and browser-origin/CSRF review.


### 2026-10-31 — Persistent learning restart proof

- PR #629 adds a fresh-process integration test using the same SQLite file and the production retrieval adapter. It proves that a published content version is retrievable after a new Python process starts, unpublished content remains excluded, and the new process writes a query trace.
- The first CI run exposed a fixture-only semantic-embedding collision (all fixture vectors were identical). The test now uses deterministic hash-derived fixture vectors; production embedding/retrieval behavior was not weakened or changed.
- Final exact-head CI on `ea13dd3fdc7a4a7da93975fe78ce2d8f7f556b2b` passed: 1738 passed/5 skipped, dashboard smoke, Windows storage regressions, and dependency audit passed. The current main post-merge CI is independently green on `f900dc014c491e2dc6a01cdcf1f22bd230c31bb4`.
- Remaining learning gates are live-model quality, human adjudication, factual verification, and operator-run staging restore/rollback; the subprocess test proves persistence mechanics only.

### 2026-10-31 — TaskRuntime partial-thread recovery

- PR [#633](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/633) separated any-thread activity from full runtime health and made startup reuse only healthy runtimes. Partial restart stops and joins surviving threads before replacements; a non-cooperative old thread blocks restart instead of permitting duplicate worker identities.
- Added regression coverage for worker-thread exit, maintenance-thread exit, and startup recovery; documented the decision in ADR-012.
- Merged to `main` as `f900dc014c491e2dc6a01cdcf1f22bd230c31bb4`. Post-merge CI passed: 1742 passed/5 skipped, Windows storage, dashboard smoke, dependency audit, artifact/link/import checks, and both deterministic benchmarks.
- Remaining operational gates are unchanged: target-host crash/soak and staging restore/rollback require the owner's environment; independent security review (Issue #52) and live-model/human adjudication cannot be self-certified.


### 2026-10-31 — Task pause/cancel precedence over retry

- PR #634 fixes failure settlement so committed pause/cancel requests are not overwritten by automatic retry. `cancel_requested` becomes terminal `cancelled`; `pause_requested` becomes `paused`; retry/backoff behavior for ordinary `running` failures is unchanged.
- Regression coverage includes task-manager state transitions and worker heartbeat-start failure, with no handler execution and explicit resume semantics. ADR-013 records the contract.
- Merged as `b50daaaaa12fab13d6526a47335613bd30a63eba`. Post-merge CI: 1746 passed/5 skipped in 211.87s; Windows storage, dashboard smoke, dependency audit, artifact/link/import checks, and both benchmarks passed.
