# Engineering Continuity Log

This file is the durable session handoff required by `docs/MASTER_ENGINEERING_PROMPT.md`. It records repository facts and evidence, not an assertion that the full project is accepted.

## 2026-10-31 — resumed engineering execution

### Source of instructions and baseline

- Operating protocol: [`docs/MASTER_ENGINEERING_PROMPT.md`](MASTER_ENGINEERING_PROMPT.md), read from branch `docs/master-engineering-continuity-prompt`, blob `f9b67598a3cec5efb5d63ecd7cb5496732576478`.
- Repository: `cognitive-kernel/Personal-AI-assistant`.
- `main` after PR #669 language-evaluator merge: `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6`.
- Previous application-code baseline: `a23760805310154d83e47489eddf00fc9b91c7f6`.
- No production database, local user data, or target-host state was accessed or modified.

### Completed work

| Task | Root cause / purpose | Result | Exact evidence |
|---|---|---|---|
| Engineering status synchronization (PR #664) | Five status/acceptance documents lagged behind PR #666 and the verified CI baseline. | Merged documentation-only update. | Merge `48aafa2a2859c74c8fe3473b8f2f1e6dfe474dfe`; exact PR head `19a95d93702d6281bdf6ae065a309a9b133de746`. |
| Durable automatic lesson follow-up (PR #667) | `_continue_lessons` kept later approved topics in process memory and persisted each run only immediately before browser work. A provider error or process interruption could leave later topics without durable queue records. | Automatic follow-up now persists all eligible approved topics as independent FIFO runs before browser capture, then relies on the existing dispatcher/lease/retry/recovery path. Added a regression proving the whole batch is queued before provider work can start. No schema migration. | Merge `6f601e8cc210419392bfbd1affb9590db978dd3a`; exact PR head `810fe450668ff6c3e0e5b74c286533abb32dfb55`. |

### CI evidence

- PR #664 exact head `19a95d93702d6281bdf6ae065a309a9b133de746`:
  - Tests: **1,800 passed, 47 deselected** in 206.89 seconds.
  - Windows storage regressions: passed.
  - Chromium dashboard smoke: passed.
  - Dependency audit: passed.
  - Markdown links: 88 files passed.
  - Import-cycle checks: passed.
  - Deterministic claim-to-test coverage benchmark: 10/10.
  - Actions: [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570053), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570102), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570079).
- PR #667 exact head `810fe450668ff6c3e0e5b74c286533abb32dfb55`:
  - Tests: **1,801 passed, 47 deselected** in 214.33 seconds.
  - Windows storage regressions: passed.
  - Chromium browser E2E: passed.
  - Dependency audit: passed.
  - Markdown links: 87 files passed.
  - Deterministic claim-to-test coverage benchmark: 10/10.
  - Actions: [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765586), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765242), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765399).
- Post-merge workflows for `48aafa2a2859c74c8fe3473b8f2f1e6dfe474dfe` and `6f601e8cc210419392bfbd1affb9590db978dd3a` were still running at the time this entry was written. Do not mark post-merge CI complete until exact-SHA runs finish successfully.

### Acceptance gates still open

- Issue [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290) remains open.
- Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52): independent security review of the Ollama loopback boundary is still required; implementation tests are not a substitute.
- Target-Windows-host baseline, load/soak, crash recovery, staging restore and rollback rehearsal remain unverified here.
- Live Persian/English model evaluation and human adjudication remain open. Deterministic/lexical CI benchmarks do not prove semantic correctness.
- Manual keyboard-only, screen-reader and rendered-state accessibility review remain open.
- Owner-reported legacy database migration completion remains owner-reported until independent target-host evidence is attached.

### Progress accounting

A provisional weighted scorecard is now defined in [`docs/ACCEPTANCE_SCORECARD.md`](ACCEPTANCE_SCORECARD.md). Using its published gate weights and stage factors, the current snapshot is **57.37% engineering completion** (`54.50 / 95 × 100`, excluding external-only gates) and **54.50% overall acceptance progress** (`54.50 / 100 × 100`). These are explicitly provisional progress measures, not release approval; weights and status assignments must be revised only with dated evidence and rationale.

### Next actions

1. Investigate source discovery/provenance reliability in the live-model answer pipeline; distinguish provider/environment failures from application defects and add a reproducible regression for any confirmed code defect.
2. Reconcile historical branches without deleting unmerged useful work.
3. Recalculate the scorecard only when new evidence changes a gate.
4. Keep target-host, human-adjudication and independent-review gates open until the responsible operator/reviewer supplies evidence.


### Continuation update — same session

- PR #668 merged as `eec03b39e53399bb8ee21a15b9e8cb4930159c25`; its final exact head was `0bcd2a93bbf4516f180ac9fc3d8c647380545ade`. Tests, Windows storage regressions, dashboard smoke, browser E2E and dependency audit passed on that head: [tests #41213673355](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673355), [browser E2E #41213673417](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673417), [dependency audit #41213673381](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673381).
- PR #668 adds this log, the weighted scorecard, and synchronizes five existing status/acceptance documents after PR #664 and #667.
- Current `main` is `eec03b39e53399bb8ee21a15b9e8cb4930159c25`; its dependency audit passed [#41215789470](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789470), while post-merge tests [#41215789440](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789440) and browser E2E [#41215789465](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789465) were still running at this update.
- PR #669 is the next code workstream: wrong-language answers were not rejected by the offline evaluator. The 65% expected-script threshold now matches the `dominant_script` classification, with symmetric 60% mixed-script rejection and exact 65% acceptance regressions. Latest head `bedf5f54bb55dd2cf64b267b9b5561436820ac61`; tests/browser E2E are running, dependency audit passed [#41215830502](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830502).


### Verified main CI completion — 2026-10-31

- Post-merge CI on `6f601e8cc210419392bfbd1affb9590db978dd3a` completed successfully: 1,801 passed / 47 deselected; Windows storage regressions; 88 Markdown files; import-cycle checks; deterministic benchmark 10/10; Chromium browser E2E; dependency audit.
- Evidence: [tests #41212956247](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956247), [browser E2E #41212956268](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956268), [dependency audit #41212956295](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956295).
- This verifies merged PR #664/#667; external target-host, live-model, human, staging/rollback, and independent-security gates remain open.


### Post-merge and PR #669 checkpoint — 2026-10-31

- PR #668 is merged. No application code or user data changed in that PR.
- At this historical checkpoint PR #669 was open; it subsequently merged as `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6` after all exact-head checks passed.


### Historical CI note clarification

The earlier sentence saying PR #664/#667 post-merge workflows were still running describes the initial checkpoint only. Those workflows later completed successfully on `6f601e8cc210419392bfbd1affb9590db978dd3a`, as recorded in the verified-main CI section.


### PR #669 completion — 2026-10-31

- Root cause: the offline evaluator had no expected-language check, so a generated answer could be lexically complete while responding in the wrong language.
- Added optional Persian/English expected-language diagnostics. Natural-language Arabic-script and Latin-script counts exclude fenced/inline code and URLs; insufficient evidence fails closed. The pass threshold is 65%, matching the existing dominant-script classification; 60% mixed-script examples fail symmetrically, while exactly 65% passes.
- Added positive and negative Persian/English benchmark cases, URL/code exclusion, insufficient-evidence and unsupported-language tests, and integration coverage. Existing evaluation cases without `expected_language` retain prior behavior.
- PR [#669](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/669) merged as `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6`; exact head `bedf5f54bb55dd2cf64b267b9b5561436820ac61`. CI: **1,807 passed / 47 deselected** in 210.52s; Windows storage regressions, dashboard smoke, browser E2E, dependency audit, Markdown validation (90 files), import-cycle checks and deterministic benchmark 10/10 passed. [Tests #41215830531](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830531) · [Browser #41215830524](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830524) · [Audit #41215830502](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830502).
- Limitations: script-ratio diagnostics do not identify natural languages semantically and do not distinguish Persian from other Arabic-script languages or English from other Latin-script languages. Live-model evaluation and human adjudication remain open.
- Current `main` is `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6`.
- Current-main post-merge CI on `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6` completed successfully: tests [#41216212310](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212310), browser E2E [#41216212276](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212276), and dependency audit [#41216212245](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212245).


### CI completion reconciliation — 2026-10-31

- The earlier pending/queued wording was accurate only at the time of its original snapshot. All three current-main workflows on `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6` subsequently completed successfully (tests, browser E2E, dependency audit).
- PR #670 documentation synchronization exact head `1d00000055573acb1edc30e402b7a8692e16ce69` also passed tests, browser E2E and dependency audit. Its changes remain documentation-only; merge after review of the final diff.
- Next implementation investigation: source discovery/provenance and live-model provider error classification. Do not claim live-model quality until a pinned model run and human adjudication exist.


## 2026-11-01 — Cloud discovery diagnostic privacy (PR #671)

- Revalidated the live repository and the master engineering prompt before continuing. `main` baseline before this change was `759ff6d25528177f234d6fb6223e6362cb055a4d`; PR #671 targeted `main` from `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6` and was one documentation-only commit behind; GitHub reported it mergeable.
- Root cause: `app/cloud_resources.py` persisted free-form exception messages and unfiltered provider diagnostics into discovery-run query results and error summaries. Those messages could contain request URLs, query parameters, proxy/network details, or other untrusted text.
- Change: persist only a bounded exception class name; allowlist provider names, endpoint/query variant labels, known failure codes, and nonnegative bounded counters on both failed and successful search paths; apply the same rule to official-reference fallback errors. Added regressions for secret-bearing URLs in search exceptions, fallback exceptions, and provider diagnostic fields.
- PR [#671](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/671) merged by squash as `2a5c83bf0ba924f4a2c8f206338a04872562d72d`. PR exact-head checks passed on `5f0df14ab092ed1f39fbcabd1e35ac46241973d6`: tests [#41221895391](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895391), browser E2E [#41221895422](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895422), dependency audit [#41221895550](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895550). The tests workflow included Windows storage regressions; both jobs completed successfully.
- Post-merge CI on exact `main` SHA `2a5c83bf0ba924f4a2c8f206338a04872562d72d` is in progress at this checkpoint: tests [#41239355370](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355370), browser/dashboard smoke [#41239355374](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355374), and dependency audit [#41239355466](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355466). Dependency audit and Windows storage jobs have passed; the main test job and dashboard smoke remain in progress. Do not record post-merge CI as complete until those two jobs finish.
- Source-provenance follow-up: static inspection of `app/chat_research.py`, `app/web_sources.py`, and `app/chat_audit.py` confirms the research path caps payloads at three HTTPS sources and the audit layer separately allowlists provider/fetch diagnostics without persisting raw query, page text, or failed-page URLs. Existing tests cover no-source fallback and diagnostic privacy. Static inspection does not explain the previously reported live run with zero structured sources; a pinned live-provider run with redacted diagnostics is still required before changing source discovery behavior.
- Open acceptance issues remain [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290) and [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52). Issue #52 still requires an independent reviewer. Target-host, staging/rollback, live-model, human adjudication, and manual accessibility gates remain open.
- At the initial post-merge inventory, no active pull requests were open. Documentation follow-up PR [#672](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/672) was subsequently opened to persist this checkpoint. Historical branches were inventoried and retained; no branch was deleted because unmerged work was not proven disposable.
- Weighted progress is unchanged: **60.47% engineering completion** and **57.45% overall acceptance progress**. PR #671 improves an already CI-verified security implementation gate but does not satisfy any external acceptance gate; no score increase is justified.


### Post-merge CI completion clarification — 2026-11-01

- The initial PR #671 checkpoint above accurately recorded the workflows as running at that time. They have since completed successfully on the exact `main` SHA `2a5c83bf0ba924f4a2c8f206338a04872562d72d`: tests [#41239355370](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355370), dashboard/browser smoke [#41239355374](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355374), and dependency audit [#41239355466](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355466). The tests workflow's Windows storage regression job also passed.
- This closes the post-merge CI follow-up for PR #671 only; it does not close the live-source, target-host, staging/rollback, accessibility, human-adjudication, or independent-security gates. Progress remains unchanged at 60.47% engineering / 57.45% overall.


## 2026-11-01 — Engineering delivery baseline and schedule control

- PR #672 was merged by squash as `348be1d2bd9f69e91f9631911f889e8ab6f442d2`; the merged checkpoint records PR #671 and its exact post-merge CI evidence.
- Current baseline for this estimate: `348be1d2bd9f69e91f9631911f889e8ab6f442d2`.
- Repository tree inventory at baseline: 412 tracked files, 285 Python files, 157 Python test files, and 83 Markdown files under `docs/`. This is inventory evidence, not proof of a completed line-by-line audit.
- A dedicated estimate was added at [`docs/ENGINEERING_DELIVERY_ESTIMATE.md`](ENGINEERING_DELIVERY_ESTIMATE.md).
- **Baseline commitment: 22 working days / 176 engineering hours**, calculated as 144 task hours plus 32 contingency hours, at 8 engineering hours per working day. Baseline start 2026-11-02; target delivery 2026-12-01, excluding public-holiday adjustments.
- Work-package estimates: repository/architecture audit 20h; architecture/API/persistence contracts 18h; AI/research/learning engineering 22h; UI functional parity and automated accessibility coverage 22h; durable-task resilience/fault injection 18h; security implementation hardening 12h; performance/dependency/CI 8h; integration/final review/documentation 24h. Subtotal 144h. Contingency 32h (22.2% of task estimate). Total 176h = 22 days.
- Scope excludes owner-run local tests, manual acceptance, human adjudication, target-host operator rehearsals, and the independent security review itself. Automated engineering simulations/tests and any code remediation remain included.
- The previous 12-day estimate is withdrawn because it had no task-level effort calculation, contingency, or auditable schedule controls. It is retained only as an unsupported historical estimate.
- The 60.47% score in the previous scorecard is not converted into hours and is not used to calculate this schedule; it remains a historical evidence-stage score.
- Forecast-change rule: preserve this original baseline; for each discovered scope change or estimate overrun, record evidence, task ID, planned/actual/remaining hours, contingency consumption, schedule impact, and linked PR/issue. Remaining days are `ceil(remaining approved hours / 8)`.
- This is a defensible planning baseline, not a measured guarantee. The estimate is intentionally subject to reforecast if the full audit identifies work beyond the 32-hour reserve. Do not silently change the original date or claim completion solely because the date passes.
