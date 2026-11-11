# Engineering Continuity Log

## 2026-11-11 — PR #709: research-first learning with gpt-oss:20b

### Root-cause analysis

- The primary local `learning-form` previously created one lesson job directly. It did not research the subject first, construct a complete ordered syllabus, or coordinate lessons as a serialized course. The fixed curriculum catalog was also presented as an enrollment path.
- The new curriculum task now discovers complementary web sources, validates and persists the outline/provenance, then creates only the first lesson execution job. The ordered syllabus itself is durable in `learning_path_topics`; subsequent execution jobs are created only after successful completion of the preceding lesson.
- A follow-up review found two completeness risks: an 800-token outline budget could truncate a 40-topic JSON syllabus, and the installer selected the learning model from environment/default values without reading the model the application would actually load from its existing SQLite settings. The latter could install a different model from the persisted setting or install legacy `qwen3:8b` immediately before migration changed it to `gpt-oss:20b`.

### Corrective changes

- Increased outline generation and its single repair attempt to a bounded 4,096 output tokens and recorded the allowance in ADR-019 and learning documentation. Both requests now require an 8,192-token context, subject to model maximum and the runtime's safe RAM budget; if the host cannot provide it, inference fails before a partial syllabus can be persisted. The JSON repair receives the same output/context budget as the first attempt.
- Normalized research query strings in every checkpoint, including the validated-outline checkpoint, so a resumed task does not discard its completed query ledger and repeat web research.
- The bootstrap now loads the project's `.env` before model resolution, reads the existing settings database read-only, installs the persisted learning model, maps only the former built-in `qwen3:8b` default to `gpt-oss:20b`, and preserves explicit custom model settings. Invalid saved model settings fail closed.
- Added regression coverage for model resolution (fresh install, persisted custom model, legacy default, environment override, malformed setting), full outline output allowance, and normalized checkpoint recovery.

### Validation

- Exact-head code commit: `bc5de1b891526930f1b4b766505843e03c499410`.
- GitHub Actions: **1,971 passed, 50 deselected**; Windows storage regressions, dashboard smoke/browser E2E, dependency audit, generated-artifact verification, 94 Markdown links, import-cycle checks, deterministic retrieval benchmark, and claim-to-test coverage benchmark (10/10) passed.
- These are repository/CI results, not a live `gpt-oss:20b` inference run on the owner's Windows host. RAM use, latency, restart recovery under real model load, and Persian/English lesson quality remain unverified. PR #709 remains Draft and unmerged in accordance with the mandatory target-host gate in MASTER_ENGINEERING_PROMPT.

---

## 2026-11-07 — Owner Windows development environment baseline

Captured from the owner's PowerShell output on 2026-11-07. This is a user-reported workstation snapshot, not a CI-runner inventory.

- Working directory: `D:\Projects\Personal-AI-assistant-main`
- Shell: Windows PowerShell
- Python: `3.11.9`
- Codex CLI: `0.166.0`
- Ollama: `0.40.13`
- Installed Ollama models at capture time:
  | Model | Model ID | Reported size |
  |---|---|---:|
  | `gpt-oss:20b-codex-64k` | `4830dbfde727` | 13 GB |
  | `gpt-oss:20b` | `f38aa0c53da5` | 13 GB |
  | `gemma4:12b` | `6114515d63c1` | 8.0 GB |
  | `qwen3:14b` | `bdbd181c33f2` | 9.3 GB |
  | `qwen3:8b` | `500a1f067a9f` | 5.2 GB |
  | `qwen2.5:7b` | `845dbda0ea48` | 4.7 GB |

Use this recorded baseline in subsequent troubleshooting instead of repeatedly asking for these version/model details. Recheck only when a relevant version or installed model is changed, or when fresh runtime evidence is specifically required. The model inventory and reported modification times are time-sensitive and must not be treated as permanent state.

---

## 2026-11-07 — PR #702: persistent Ollama context and reliable Codex smoke evidence

### Evidence-based diagnosis

- `main` now includes PR #701 as merge commit `a9c4522e94cf1558b616ca98510d1b8654e8e53e`; its durable-task and recovery changes are not being repeated here.
- Windows diagnostic run [42355938096](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42355938096) confirmed that the non-admin Runner can execute the official MXC compatibility probe and direct `cmd.exe` + Python probe. Those checks returned `MXC_SERVICE_OK` and `MXC_CMD_PYTHON_OK`.
- The same run's Codex JSONL summary recorded five command starts/completions, zero agent messages, a completed turn, and 33,753 input tokens. The run failed the acceptance gate; it did not prove that the Codex tool path worked.
- The previous context preflight sent `options.num_ctx=64000` only on its own `/api/generate` request. Codex later opens independent requests for the selected base model, whose effective default remained 8,192. Ollama's current guidance requires at least 64k for coding tools and supports a persistent `PARAMETER num_ctx` in a derived Modelfile.
- The previous evidence parser required exactly one model-generated command and read the command string only from the completion event. That is brittle: command counts are model-controlled, and the Codex JSONL lifecycle must be correlated by item ID rather than assuming all fields are repeated in both events.

### Corrective changes on the dedicated PR branch

- `scripts/verify_ollama_context.ps1` now creates a separate `<selected-model>-codex-64k` model variant through Ollama's local `/api/create`, derived from the selected model with persistent `num_ctx=64000`. It verifies the saved Modelfile parameter through `/api/show`, loads the variant without request-level context overrides, and checks actual allocation through `/api/ps`.
- The original selected model and Ollama server settings remain unchanged. The alias shares the base weights; no second model download, server restart, administrator credential, or privilege change is required. If 64k cannot be allocated, the workflow fails before Codex.
- Both the Windows diagnostic and autonomous engineering workflows now pass the derived model to Codex and re-load/verify its saved 64k default after Codex execution.
- `codex_smoke_evidence` now correlates command start/completion events by ID, uses the start event's command when the completion omits it, requires every observed command to complete successfully, and requires exactly one command to prove the complete workspace create/read/delete marker. It no longer requires the model to emit exactly one command overall.
- Evidence JSON contains only bounded status/exit-code/fragment booleans and IDs; raw command text, output, and agent-message text are not persisted.
- The diagnostic smoke no longer requires a final natural-language agent message: its contract is a completed, non-failed turn plus verified workspace command evidence. This does not weaken real autonomous task completion, which still requires the exact machine-readable task marker and owner-verified PR lifecycle.
- Regression coverage now includes completion events without a repeated command, multiple successful command events with one verified probe, failed/mismatched/duplicate events, redaction, and persistent-context enforcement in both workflows.
- The diagnostic workflow is enabled for this dedicated branch so the self-hosted Windows job can be dispatched against the PR head.
- Diagnostic cleanup now queries the actual `GITHUB_REF_NAME`, cancels only older queued runs, and preserves an active sandbox job. The Codex probe uses non-login `cmd.exe`, a bounded 25-minute step/30-minute job timeout, and an exact branch/SHA run label.
- Item-level Codex error events are retained in smoke evidence and fail closed; the diagnostic smoke's final natural-language message is optional, while actual autonomous task completion markers remain mandatory.

### Validation and residual risk

- Exact-head GitHub CI for the updated PR head is pending; no test result is inferred from earlier commits.
- The real Windows Runner diagnostic must pass on the final PR head, including the post-Codex 64k allocation check. The prior run remains a failure and is not retroactively reclassified.
- Target-host memory/performance soak at 64k, full autonomous task execution, restart/recovery, staging restore/rollback, and owner acceptance remain open.
- PR #702 remains unmerged. No direct write to `main`, unsandboxed fallback, elevation, or automatic merge was performed.
- Issue #290 remains the overall acceptance tracker; Issue #52 still requires independent security review.


## 2026-11-02 — PR #680: continuous autonomous engineering controller

- Current implementation branch: automation/codex-engineering-agent-rebased; current PR head: c943990de467694bc39bcee06738d75446007a73; base main: 60597e13e70a3014f6a1871290cf843d188ca45e. PR #680 is open, mergeable, and not yet merged.
- The last fully validated code head before the latest documentation/dashboard refinements was abdbf7ba738a1ec72078bc4c5acb834e29c7a270. Its exact-head CI passed: 1,871 tests passed, 49 deselected; Windows storage regressions passed; Chromium dashboard smoke passed; dependency audit passed; fail-closed claim-to-test evaluation passed 10/10 with zero false accepts. Runs: tests + Windows storage #41517918282, browser E2E #41517918348, dependency audit #41517918279. The current PR head c943990de467694bc39bcee06738d75446007a73 has new exact-head checks in progress and must be revalidated before merge.
- The trusted controller performs one bounded engineering task per Windows self-hosted workflow run. Codex uses local Ollama and receives no GitHub workflow token. The controller alone creates/publishes a non-draft PR, dispatches independent exact-SHA checks, verifies unchanged PR head and main base, merges the validated SHA, verifies the resulting main commit, and dispatches the next run.
- Continuous mode is the default. It self-starts after a main update or hourly heartbeat when no other run is active. A duplicate-run guard suppresses overlapping manual, scheduled, and recovery triggers. Scheduled mode supports exactly 1, 2, 4, 6, 12, or 24 hours. There is no overall task-count or run-count cap; each individual GitHub Actions job remains bounded by the six-hour platform limit.
- The Windows checkpoint watcher persists allowlisted worktree changes and the structured agent plan outside the repository. Recovery restores only against the exact immutable base SHA. Manual tasks are persisted in a repository Actions variable until a confirmed merge. Failed/timed-out cycles retry indefinitely with exponential backoff of 30, 60, 120, then 240 seconds; only the owner's explicit Pause control stops the queue.
- Dashboard controls include settings, manual task dispatch, pause, resume, emergency cancel, live plan/phase progress, and scorecard history. Progress is reconciled with the actual single-job workflow; legacy awaiting_review status no longer freezes the dashboard.
- The code and GitHub-hosted checks are verified on the exact head abdbf7ba738a1ec72078bc4c5acb834e29c7a270; this does not prove the user's Windows host is provisioned or that a real self-hosted cycle has completed. Runner service/account, shared-storage ACL, GitHub CLI browser authorization for the local dashboard, Ollama model/context readiness, first live cycle, soak/recovery/rollback, human acceptance, and independent security review remain external operational gates. No deployment or database mutation is performed.
- Classic branch-protection details could not be read through the connected GitHub integration. No branch-protection bypass or disabling was attempted; the controller will fail closed if repository policy rejects the merge.



This file is the durable session handoff required by `docs/MASTER_ENGINEERING_PROMPT.md`. It records repository facts and evidence, not an assertion that the full project is accepted.


## 2026-11-01 — PR #680 final pre-pull engineering review

- Current base: `main` SHA `60597e13e70a3014f6a1871290cf843d188ca45e`. The feature remains isolated on `automation/codex-engineering-agent-rebased`; no direct-main write, merge, deployment, or history rewrite was performed.
- Exact reviewed PR head: `c7ec867df63baac9dec74b736b65a64284a525e4`; PR [#680](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/680) is open, Draft, and unmerged.
- Final review corrected progress semantics: the displayed plan percentage is completed/total structured Codex plan items (equal-weight task count, not estimated effort). Workflow phase and active step are separate; plan completion may reach 100% before validation and PR publication finish.
- Project history is evidence-safe: the workflow snapshots the trusted `main` scorecard before Codex starts; the original baseline is persisted separately; new points are added only when accepted scorecard percentages change on `main`. Unmerged candidate scorecard changes and unchanged percentages do not create chart points.
- Live progress starts before prerequisite checks. The current plan percentage is retained through tests, quality gates, GitHub validation, and draft-PR publication. The workflow fails closed if progress telemetry cannot be persisted.
- Security hardening: candidate patches cannot modify `pyproject.toml`; Codex explicitly selects Ollama with `--oss --local-provider ollama`; app and non-admin Runner accounts have separate checks; the Runner is rejected if it is a local administrator; the Runner account does not use GitHub CLI authentication; the Runner receives Modify on telemetry while the app receives read/execute only.
- Windows setup documentation now distinguishes app and Runner accounts, includes separate readiness scripts, explains the 64k Ollama context requirement, and identifies target-host checks that remain external.
- Exact-head CI for `c7ec867df63baac9dec74b736b65a64284a525e4` passed:
  - Tests: **1,840 passed, 48 deselected**.
  - Deterministic lexical retrieval benchmark: passed.
  - Fail-closed claim-to-test coverage benchmark: **10/10**, zero failed cases.
  - Windows storage regressions: passed.
  - Chromium dashboard smoke: passed.
  - Dependency audit: passed.
  - Actions: [tests and Windows regressions](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41268589102), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41268589078), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41268589074).
- Pull readiness is limited to checking out the feature branch. The PR is not merged. Before enabling scheduled operation or merging, the owner must complete Windows Runner registration, verify the dedicated non-admin account and ProgramData ACLs, validate the 64k Ollama context and performance/soak on the target host, and obtain the independent security review. Manual keyboard/screen-reader and target-host usability reviews also remain open.

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

## 2026-10-31 — Engineering baseline reconciliation and DuckDuckGo redirect parsing (E-03)

- PR #674 merged to `main` as `d876b3f568ddb588f66a3b6718970d5b38c41ab0` (parent `348be1d2bd9f69e91f9631911f889e8ab6f442d2`). Its exact head `826a73accc91d515e53c77f7286c4fa8de185a97` passed tests, Windows storage regressions, dashboard smoke, and dependency audit (runs #41246237596, #41246237597, #41246237622). The Codex review attempt reported a usage-limit failure; GitHub formal reviews and review threads were empty, `main` was unprotected, and the repository rulesets endpoint returned no rulesets. No independent-review approval is claimed.
- PR #673 then merged to `main` as `bcb04de842faab596d31c3955fa5046a407c9fc7` (parent `d876b3f568ddb588f66a3b6718970d5b38c41ab0`), adding the estimate referenced by PR #674. Exact-head checks passed (tests/Windows storage #41246032321, dashboard smoke #41246032340, dependency audit #41246032319). All four post-merge checks on current `main` `bcb04de842faab596d31c3955fa5046a407c9fc7` passed (tests/Windows storage #41246877787, dashboard smoke #41246877793, dependency audit #41246877791).
- E-03 static review found a reproducible URL-integrity defect in `app/web_sources.py`: `urllib.parse.parse_qs` already decodes DuckDuckGo's outer `uddg` parameter, but the parser decoded the target URL a second time. A target query such as `q=alpha%2Bbeta%26mode%3Dstrict` was changed into separate query parameters during normalization. The Google parser already documents why a second decode must not occur.
- PR #676 (draft): [preserve encoded DuckDuckGo redirect targets](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/676). Implementation commit: `f0ff075e9474817cd6fdf643f30087c98b93761f`; regression-test commit: `1b1f0229cd62b3109928da3455a445992d209d7f`. The test exercises redirect parsing through the URL normalizer and verifies encoded reserved characters remain one query value. At this checkpoint, exact-head `pip-audit`, `dashboard-smoke`, and Windows storage checks passed; the main test workflow was still running. The branch remains draft until final-head CI and review gates are revalidated.
- The old roadmap HTTPS-policy row was corrected in the same PR: PR #622 merged as `c46e136bb00e4dd56627e98990b80d27f73b93e9`; its implementation is not still in review.
- E-03 baseline allocation remains 22 h. The planned start is 2026-11-02; this pre-start automation run does not book baseline hours. Runtime engineering time is not metered here, so no actual-hour figure is invented; no contingency consumption or schedule reforecast is recorded.
- External acceptance remains open: the pinned-model live Persian/English evaluation and human adjudication, target-host operations/recovery, staging restore/rollback, and independent security review in Issue #52. The parser regression does not establish live provider availability or resolve the historical zero-source evaluation by itself.

## 2026-11-01 — Local Ollama autonomous engineering console (PR #675)

- Replaced the paid OpenAI API workflow design with a Windows self-hosted runner using Codex CLI `--oss` and local Ollama; no `OPENAI_API_KEY` is required.
- Added a dedicated `/autonomous-engineering` operations page, local read-only diagnostics, explicit GitHub workflow dispatch/cancel actions, and a PowerShell readiness check.
- Added the agent destination to the main dashboard navigation and shared primary navigation to the standalone AI-learning and autonomous-engineering pages.
- The workflow runs local Codex only on the labeled trusted-main self-hosted runner; candidate validation and draft-PR publication remain separate GitHub-hosted jobs.
- Implementation and CI are in progress on `automation/codex-engineering-agent`. No test pass, runner installation, or target-host operation is claimed yet. The local runner must be registered and kept online by the repository owner.


### Follow-up — live progress telemetry and scorecard history

- Added a dedicated progress API and compact dashboard panel. The current cycle reports the active Codex plan item, completed/total plan steps, workflow phase, and progress percentage; later GitHub-hosted validation/publication stages are reconciled from the run's actual job and step states.
- Codex JSONL events are reduced to bounded activity breadcrumbs. Raw command output, credentials, and model reasoning are not persisted or shown. The temporary `.agent-progress.json` plan is excluded from the candidate patch.
- Added daily, weekly, and custom date-range SVG charts for the weighted engineering-completion and overall-acceptance scorecard metrics. The existing 2026-11-01 scorecard is the only historical baseline; new snapshots are recorded on worker-run dates, with no fabricated backfill.
- Activity events and project score snapshots use separate bounded local files under `%ProgramData%\\PersonalAIAssistant\\autonomous-engineering`. A one-time ACL script grants access only to the dedicated Runner account, local application account, SYSTEM, and local administrators. The UI polls the small progress endpoint every five seconds; logs remain collapsed by default and no charting dependency is added.
- Added API, scorecard parsing, plan-progress, GitHub step reconciliation, and compact UI regression tests. Exact-head CI and Windows ACL/runner verification remain pending; no operational telemetry is claimed until the shared directory is configured and a real workflow run reports events.


### Branch-continuity correction — 2026-11-01

- Two progress helper files were inadvertently committed directly to `main` while a branch argument was omitted from two GitHub Contents API updates: `f7d38e99ec717c35404c95eea535171e4c6e3280` added `app/autonomous_engineering_progress.py`, and `60597e13e70a3014f6a1871290cf843d188ca45e` added `scripts/autonomous_engineering_progress.py`. These commits changed only those two new helper files; they did not alter existing application routes or activate the feature.
- The mistake was not hidden or force-rewritten. The replacement PR #680 is based on the resulting current-main SHA and updates both helpers together with the full UI, API, workflow, tests, and documentation integration. The superseded PR #675 was closed after #680 was opened.
- All subsequent writes explicitly target `automation/codex-engineering-agent-rebased`. No direct-main write, force-push, or history rewrite is planned.


## 2026-11-01 — Local Ollama autonomous engineering console: exact-head verification (PR #680)

- Revalidated live repository state: `main` is `60597e13e70a3014f6a1871290cf843d188ca45e`; PR [#680](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/680) targets that exact base from `automation/codex-engineering-agent-rebased` at head `e3c8702db7eb21689f15f01232dca7c76ea137c7`. The PR is mergeable and remains unmerged.
- Reviewed the complete feature implementation surfaces: FastAPI status/settings/dispatch/cancel/progress routes, loopback dependency, Ollama diagnostics, bounded progress reader/writer, Windows Runner preflight and ACL scripts, RTL dashboard JavaScript/HTML, workflow isolation, regression tests, and setup documentation.
- Exact-head automated verification passed on `e3c8702db7eb21689f15f01232dca7c76ea137c7`: tests workflow [#41269501876](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501876) including `windows-storage-regressions` and the main test job; real Chromium dashboard smoke [#41269501826](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501826); dependency audit [#41269501856](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501856). Every job completed with `success`.
- No local target-machine test, human acceptance test, human model adjudication, or independent security review was performed. These are intentionally deferred to the owner/reviewer after engineering handoff; Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) remains open until independent review evidence is recorded.
- Operational prerequisites remain external: register the Windows self-hosted Runner under a dedicated non-administrator account; configure the shared ProgramData ACL for the exact Runner and app identities; ensure Ollama and the configured model are available to the Runner account; execute the target-host soak/recovery and rollback rehearsal. CI smoke tests do not prove these host-specific prerequisites.
- No acceptance-score increase is recorded: the PR adds a new operations path, but it does not close project-wide UI parity, operational, human, or independent-review gates. The historical scorecard values are not inflated.
- The two accidental helper commits on `main` remain documented in the prior branch-continuity entry; no history rewrite or force-push was performed.

## 2026-11-01 — Autonomous engineering console hardening and direct task submission (PR #680)

- Added the missing direct-task form to /autonomous-engineering. A bounded request (maximum 2,000 characters) is sent through the local app's authenticated GitHub CLI as a workflow_dispatch input; the workflow validates it again and places it in the ephemeral untracked request file. Codex is explicitly instructed to treat the task as untrusted data, and the existing allowlisted patch/independent-validation boundaries remain unchanged.
- The page warns that task text is part of GitHub workflow event metadata; users must not submit passwords, tokens, API keys, private user data, or secrets. The periodic worker continues selecting work from the project ledger when no direct task is supplied.
- Security review found the one-time progress ACL script only applied inheritance removal to the root directory, not existing descendants. Updated it to apply the restricted ACL recursively (/T /C) and added a regression assertion. Existing descendants are now included when the script is rerun; new files inherit the configured directory ACL.
- Final exact-head verification on 016f0004afa7443660868d0ba93eca9fdad7f649: tests [#41312855384](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855384) passed **1,842 tests / 49 deselected** in 199.52 seconds; Windows storage regressions passed; browser E2E [#41312855370](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855370) passed, including the real-browser manual-task submission regression; dependency audit [#41312855311](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855311) passed. Generated learning artifacts, Markdown links (92 files), import-cycle scan, lexical retrieval benchmark, and claim-to-test benchmark (10/10) also passed.
- The exact-head tests workflow had one intermediate failure on the preceding SHA caused by misplaced test assertions (NameError in a test); the assertions were moved to the test that defines the workflow fixture, and the final exact SHA above is green. No production-code failure remained from that intermediate run.
- No local target-machine test, human acceptance test, human model adjudication, or independent security review was performed. Owner-side Windows Runner registration, dedicated non-admin identity, Ollama/model readiness, target-host soak/recovery/rollback, and independent Issue #52 review remain external acceptance gates.
- No scorecard increase is claimed: this console and its automated regressions do not close the project-wide UI parity, target-host, human-quality, staging/rollback, or independent-review gates. The project remains below 100% overall acceptance.

### Exact-head verification update — implementation SHA `91542f14ad51b2662528b45b602d43ac40d03e35`

- The exact implementation head passed all four GitHub checks: tests [run 41319480167](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319480167), browser E2E [run 41319480147](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319480147), dependency audit [run 41319480179](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319480179), and Windows storage regressions in run `41319480167`.
- Test suite: **1,860 passed, 49 deselected** in 142.52 seconds. The Windows storage regression job passed. Browser dashboard smoke passed. Dependency audit passed. Learning artifact validation, Markdown links (92 files), import-cycle check, lexical retrieval evaluation, and claim-to-test acceptance benchmark (10/10) passed.
- The retry regression test verifies that a failed full test suite causes a fresh Codex session to repair the same task before publication. Token-isolation and protected-path tests also passed.
- These results verify the implementation SHA above. Documentation-only checkpoint updates are being added separately and must receive their own exact-head CI before pull readiness is declared. PR #680 remains open and unmerged.

### Final exact-head verification — `9018fefb128738a0c22a92a25482ec6393b4c29e`

- Latest PR head `9018fefb128738a0c22a92a25482ec6393b4c29e` passed all four checks: tests + Windows storage [run 41319954134](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319954134), browser E2E [run 41319954115](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319954115), and dependency audit [run 41319954116](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41319954116).
- Full suite: **1,860 passed, 49 deselected** in 202.78 seconds. Windows storage regression job passed. Browser dashboard smoke and dependency audit passed. Generated learning artifacts passed; Markdown links passed for 92 files; AST import-cycle checker found no cycles; lexical retrieval and claim-to-test benchmark (10/10) passed.
- The test suite includes both local full-suite failure retry and GitHub-hosted CI failure retry coverage. No task advances until the same task passes again and its PR merges.
- PR [#680](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/680) remains open, unmerged, and ready for the owner's pull/local acceptance test. No merge or direct-main write was performed by this session.
## 2026-11-05 — ریشه‌یابی و اصلاح Checkpoint Watcher (PR #689)

- اجرای واقعی Windows workflow run 42020328969 روی main در SHA c31dd8cd0dbac4f85672e07ef82f41a9cb4d3329 فایل Metadata دارای run_id، task و watcher_pid را ثبت کرده بود، اما controller به‌دلیل الزام هم‌زمان فایل جداگانهٔ .ready پس از ۳۰ ثانیه شکست خورد. علت دقیق OS-level برای منتشرنشدن Marker از آن لاگ اثبات نشد؛ اصلاح بر حذف وابستگی معماری به آن عملیات دوم متمرکز شد.
- قرارداد Metadata به ماژول مستقل scripts/autonomous_engineering_checkpoint_contract.py منتقل و schema به نسخهٔ ۳ ارتقا یافت. آمادگی تنها با یک atomic replace ثبت می‌شود و controller همان run/task/base SHA/PID را اعتبارسنجی می‌کند. نوشتن .ready از مسیر watcher حذف شد؛ markerهای قدیمی همچنان در شروع/پاک‌سازی حذف می‌شوند. Metadataهای نسخهٔ ۲ برای بازیابی قبلی همچنان خوانده می‌شوند.
- یک race مستقل و مرتبط نیز رفع شد: snapshot قبلاً برای شناسایی فایل‌های جدید git add -N را روی index مشترک اجرا می‌کرد، درحالی‌که Codex هم‌زمان روی همان worktree کار می‌کند. اکنون هر snapshot یک Git index موقت و مستقل از base SHA می‌سازد و پس از diff حذف می‌کند؛ .git/index مشترک دست‌نخورده می‌ماند.
- قفل بین‌فرایندی repository_execution_lock در controller حفظ شد؛ watcher عمداً آن قفل را نمی‌گیرد، چون باید هنگام تغییر worktree توسط Codex snapshot بگیرد. مالکیت watcher از Metadata run/task/PID و بازیابی از controller تحت قفل، base SHA ثابت، بررسی process command line و git apply --check پیروی می‌کند. این قرارداد در ADR-014 ثبت شده است.
- Regressionها شامل readiness بدون marker، تطبیق run/task/base/PID، عدم تغییر shared Git index، process integration، جداسازی checkpointها، قفل بین‌فرایندی و restore هستند.
- PR #689 باز و به main هدف‌گذاری شده است. اجرای focused regressions روی head میانی 1a979cbf04855ec3264a2559970b9cb193a2ea8a موفق شد؛ اجرای full suite، Windows storage، browser E2E و dependency audit برای head نهایی هنوز باید تأیید شوند. هیچ ادغام یا موفقیت روی میزبان واقعی ادعا نمی‌شود.
- این کار بخشی از E-05 است؛ baseline 176 ساعت و forecast تغییر نکرده‌اند. ساعت واقعی مهندسی در این checkpoint ثبت نشده و از مدت مکالمه به‌عنوان actual استفاده نشده است.


## 2026-11-06 — Autonomous engineering queue durability and dispatch-state hardening (PR #701)

- Root-caused a manual-task loss path: the workflow previously persisted a submitted task only after Python/Ollama/Codex/sandbox preflight. A failure before capture meant the recovery workflow dispatched an empty input with no durable copy of the task. Added a dedicated PowerShell persistence step before every runner prerequisite check; the later Python capture remains a second validation boundary.
- Prevented task replacement and cross-task cleanup: a different pending manual task is rejected; a new manual task is rejected while a controller-authored autonomous PR remains open. Empty recovery dispatches continue loading the durable pending task. The persistence helper is protected from Codex edits.
- Removed the obsolete `AUTONOMOUS_ENGINEERING_FAILURE_STREAK` write from the resume route. Recovery now derives the streak from workflow history, so resume no longer depends on a variable that no consumer reads.
- Aligned the dashboard and cancellation run windows with the schedule gate's 30-run window. The UI displays the five newest runs plus any older active runs, avoiding a stale queued run disappearing behind newer completed runs.
- Exact-head verification for implementation SHA `c8379f694c503a32ecabab0f53ff306fc26f840c`: tests [run 42248513636](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42248513636) passed **1,899 tests / 50 deselected**; Windows storage regressions passed; browser smoke [run 42248513521](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42248513521) passed; dependency audit [run 42248515763](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42248515763) passed. Learning artifacts were current, Markdown links passed for 93 files, no import cycles were detected, and the deterministic acceptance benchmark passed 10/10.
- The exact-head CI above predates a final test-only assertion correction. The subsequent current branch head must receive its own exact-SHA CI before this PR is considered ready.
- PR [#701](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/701) remains open and unmerged. No merge or direct-main write was performed.
- The independent Windows service-account Codex sandbox smoke remains pending under PR [#699](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/699). GitHub-hosted CI does not prove the real self-hosted Runner can execute the Codex workspace tool. No target-host success is claimed.
- Actual engineering hours for this checkpoint were not measured; no time-based actual is inferred from conversation duration. No acceptance-score increase is recorded.

## 2026-11-07 — Fix manual-task persistence on clean Windows Runner workspaces

- Root cause: the workflow invoked `scripts/persist_autonomous_manual_task.ps1` from `GITHUB_WORKSPACE` before running `actions/checkout`. A newly provisioned or cleaned self-hosted Runner could therefore lack the script at the point of invocation, preventing the durable task from being saved.
- Correction: move the pinned, trusted-`main` checkout ahead of the persistence step while keeping persistence ahead of system-Python, Ollama, and Codex preflight. Checkout uses `persist-credentials: false`; the workflow token is not persisted in Git configuration.
- Regression: extend `test_manual_task_persistence_precedes_every_runner_prerequisite_check` to require the order checkout < persistence < Python preflight < live-state capture, and assert checkout credential persistence remains disabled.
- Implementation commit: `e1d47d05bc30cf536e1f8a98bd8e272d4590b53a`; regression commit: `0745b50e30a38a08421d6083af8b16eaa750e0c0`.
- Static source/order validation passed. Full pytest, Windows storage regressions, browser E2E, dependency audit, and exact-head CI for the updated branch are pending; no test or target-host success is claimed for these follow-up commits.

## 2026-11-07 — Persist manual tasks before schedule gating and serialize only Codex execution (PR #701)

- Additional root cause: manual-task persistence was inside the Codex job, which depended on `schedule-gate`. A paused schedule or active-run suppression could skip that job entirely, losing the submitted task before any durable copy existed.
- Correction: move persistence into a dedicated Windows job that checks out pinned trusted `main` with `persist-credentials: false` and writes the task to the ProgramData pending-task file before `schedule-gate`. The gate proceeds only when persistence succeeded or was intentionally skipped for a non-manual event; the Codex job requires both persistence and gate completion. Manual persistence is restricted to non-empty `workflow_dispatch` events targeting `main`.
- Additional queue-loss prevention: remove workflow-level concurrency, which permits a newer pending workflow dispatch to replace an earlier dispatch before its persistence job runs. Keep serialization at the Codex job, and retain the active-run gate plus `repository_execution_lock` to prevent overlapping engineering work.
- Regression coverage asserts persistence-job ordering before the schedule gate, trusted-main checkout, disabled credential persistence, exact Windows Runner labels, Codex job dependencies, main-only dispatch, and the absence of workflow-level concurrency.
- Integrated the verified JSONL Codex command-execution evidence parser, regression tests, and service-account sandbox diagnostic from PR #699. The diagnostic uses the same `self-hosted, Windows, X64, ollama` labels as the autonomous Codex job and checks the non-admin service identity, MXC compatibility, explicit `cmd.exe` execution, workspace create/read/delete, final agent response, and cleanup.
- Exact-head verification for implementation SHA `71d5fcb5e5be9c451c9c5da5273b0bbd058d2b11`: tests [run 42339427554](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42339427554) passed **1,911 tests / 50 deselected**; Windows storage regressions passed; browser E2E [run 42339427715](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42339427715) passed; dependency audit [run 42339427722](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42339427722) passed. Learning artifacts, 93 Markdown files, import-cycle checks, lexical retrieval benchmark, and claim-to-test benchmark 10/10 with zero false accepts passed.
- A subsequent main-ref guard and documentation reconciliation are being committed after that verified code SHA; they require their own exact-head CI before merge readiness is declared.
- The real Windows service-account Codex smoke has not yet passed. Preliminary run [42338759730](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42338759730) remains in progress; strict-label run [42339406012](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42339406012) is queued. No target-host success is claimed.
- Owner-side local Windows validation, soak/recovery/rollback, human acceptance, and independent security review remain open. PR #701 remains unmerged; no acceptance-score increase is recorded.

## 2026-11-07 — Codex context allocation root cause and enforced preflight

- The real Windows Runner diagnostic [run 42346702200](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42346702200) established the exact mismatch: model metadata advertised 131,072 tokens, but Ollama's active `/api/ps` allocation was only **8,192 tokens**. The strict diagnostic correctly failed before starting Codex; its follow-up independently reported the same 8,192-token runtime allocation.
- Root cause was the warm-up/preflight not requesting a larger runtime context. Ollama's API supports the `options.num_ctx` generation parameter and exposes the actual loaded allocation as `/api/ps.models[].context_length`; see [API request options](https://github.com/ollama/ollama/blob/main/docs/api.md) and [API schema](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml).
- Updated `scripts/verify_ollama_context.ps1` to explicitly request `options.num_ctx=64000`, keep the model resident, and fail closed unless the loaded runtime actually reports at least 64,000 tokens. Both production and diagnostic workflows run this gate before any Codex task. The regression test asserts the explicit request and gate ordering.
- The Windows diagnostic has not yet been rerun against this updated preflight; it must prove that the target host can allocate 64k and then pass the real Codex JSONL tool-execution smoke. If the host cannot allocate the requested context, no task is dispatched to Codex.
- No PR has been merged. The current PR head must pass its complete exact-head CI and a new real Windows Runner diagnostic before acceptance.

## 2026-11-07 — Real Windows Runner result and Codex provider diagnosis

- Preliminary service-account diagnostic [run 42338759730](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42338759730) failed closed. It confirmed the Runner is non-admin (`runner-svc`), Codex CLI `0.167.0`, Ollama `0.40.13`, and direct MXC plus `cmd.exe`/Python execution succeed. The actual Codex turn completed without a final agent message or a command-execution event; this is not a successful sandbox result.
- The diagnostic now records safe JSONL event/item types, command/agent counts, and token usage. An `if: always()` post-step reads Ollama `/api/ps` and `/api/show` metadata to inspect the active context and model `num_ctx` after the Codex attempt. The Ollama integration documentation requires at least a 64k-token context window; the target Runner's actual context has not yet been verified.
- Strict-label service-account run [42338994817](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42338994817) is in progress; context/evidence follow-up [42339968999](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42339968999) is queued. No Codex tool-execution success is claimed.
- Exact-head CI for code checkpoint `81464adea1ae37ed745b37ecb4c2010a87d9c273` passed: **1,911 passed, 50 deselected**; Windows storage regressions, browser/dashboard E2E, dependency audit, generated artifacts, 93 Markdown files, import-cycle scan, lexical benchmark, and claim-to-test benchmark 10/10 with zero false accepts passed. Subsequent documentation-only updates require fresh CI on the resulting head.
- PR #701 remains unmerged. Owner-side Windows acceptance, soak/recovery/rollback, and independent security review remain open; no acceptance-score increase is recorded.


## 2026-11-07 — Runtime context gate and owner-reviewed PR lifecycle

- Real Windows diagnostic [run 42340997965](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42340997965) established the Codex failure's root cause: Ollama `/api/ps` allocated only 8,192 tokens to `gpt-oss:20b`, although `/api/show` metadata advertised a 131,072-token maximum. The Codex turn emitted no final agent message and four command-execution items. Direct non-admin MXC and `cmd.exe`/Python probes passed; strict sandbox acceptance did not.
- Added `scripts/verify_ollama_context.ps1`. It warms the selected model on `127.0.0.1:11434`, reads `context_length` from `/api/ps`, and fails closed below 64,000 tokens. The production automation workflow and Windows diagnostic run this before any Codex session. The target host must set Ollama Context Length to at least 64k and restart/reload the server/model; the non-admin Runner cannot safely change the service configuration.
- Production and diagnostic probe paths are unique per workflow run and attempt; regression tests assert exact JSONL command evidence and no stale static probe path.
- Repository policy issue #290 explicitly prohibits automatic PR merges. Removed controller calls to the GitHub PR merge endpoint. Exact-head CI now results in an open PR for owner review. Added atomic durable `pending-autonomous-pr.json` state; task/checkpoint are retained until the owner manually merges. The next controller run verifies the exact merge SHA, runs tests/browser E2E/dependency audit against that exact post-merge SHA, then clears pending task/checkpoint and advances continuous mode. Closed-unmerged or changed PRs fail closed.
- Updated the early manual-task persistence script to reject new task replacement while pending PR/post-merge state exists. Updated workflow prompt and operations guide to reflect owner review and post-merge validation.
- Exact-head CI for the current PR head is recorded in the PR body. Strict real-runner Codex execution and owner-side Windows acceptance remain blocked until the 64k runtime context is configured. PR #701 remains open; no merge or acceptance-score increase is claimed.

## 2026-11-10 — Research-first local learning curriculum (feature branch)

- **Root cause:** the primary `learning-form` submitted a single lesson job. It could discover web sources for lesson generation, but it had no durable outline-first curriculum stage. Preset topics were exposed separately through the built-in curriculum selector. The Chrome/ChatGPT browser-learning implementation was a separate subsystem and was not the correct target.
- **Decision:** keep the browser-learning subsystem unchanged; make the primary local learning form research the requested topic, derive and validate a comprehensive ordered outline, persist source provenance, and generate lessons sequentially.
- **Implementation:** added durable task type `learning.plan_curriculum`, multi-query web research with a minimum of three usable pages across two domains, strict 8–40 topic validation, schema migration 42 for the outline/model/source provenance, and a per-course queue that advances only after the previous lesson succeeds. Failed/rejected/cancelled lessons block automatic advancement; explicit resume retries without deleting old lesson records.
- **Model policy:** default the dedicated learning model to `gpt-oss:20b`, install and verify it during bootstrap, and prevent cost-profile selection from silently switching to another model. Chat and programming model settings remain separate.
- **Data safety:** preset enrollment controls are removed from the primary UI, while legacy curriculum APIs, existing paths, lesson records, assessments, and history remain intact.
- **Validation status:** implementation and regression tests are being prepared on `feature/researched-learning-curriculum-gpt-oss-20b-20261110`. GitHub Actions and target-host validation have not yet completed. Do not merge until exact-head CI and the required host gate pass.


## 2026-11-11 — Unlimited, version-aware learning curricula (PR #710)

- Root cause: the previous curriculum workflow treated a single model response as the complete course and imposed a total 8–40 topic limit. This confused a safe per-request batch size with a syllabus-wide ceiling.
- Replaced the total ceiling with resumable, checkpointed batches. The course continues until the model explicitly reports comprehensive coverage; duplicate-only/no-progress batches fail closed instead of falsely completing. Batch size remains bounded per model request for predictable memory and output use, not as a limit on course size.
- Added structured per-topic applicability and provenance: product/version/build, edition, compatibility level, platform, execution mode, prerequisites, permissions, limitations, hierarchy, exact source URLs, retrieval timestamp, and publication/update dates when actually known. Unsupported source references downgrade applicability to unverified. Migration 43 adds backward-compatible fields and preserves existing learning data.
- Set the default learning model to `gpt-oss:20b`, while preserving explicit user model choices and environment overrides; model selection is pinned so the learning lane cannot silently substitute a different installed model.
- Lesson generation receives the course's exact source/version scope and is instructed to distinguish engine release from compatibility level, cite only supplied URLs, and mark unsupported conditions unverified. The prompt fingerprint and model/usage provenance remain recorded.
- Exact-head SHA `d18fceea301c51ef56e3731b03055af446c2be04` passed all required GitHub workflows: tests + Windows storage [#42992647418](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42992647418), browser E2E [#42992648348](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42992648348), and dependency audit [#42992647476](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42992647476). The test workflow also completed generated-learning-artifact, Markdown-link, import-cycle, retrieval benchmark, and fail-closed claim-to-test coverage steps successfully.
- PR #710 remains open and unmerged. Per the repository's engineering contract, this CI evidence does not substitute for target-host validation. As requested, no local/human test was awaited or claimed; actual `gpt-oss:20b` loading, resource use, restart recovery, and live lesson quality remain explicitly unverified. Project-wide acceptance remains tracked by Issue #290; independent security review remains open under Issue #52.
