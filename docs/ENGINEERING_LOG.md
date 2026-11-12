## 2026-11-12 — Per-user Ollama path and target readiness coverage

- Root cause: a non-admin Windows Runner may not receive Ollama's per-user install directory in PATH, and WMI can omit ExecutablePath for a process owned by another account. The old fixed Administrator path was not portable.
- Fix: runner readiness, provisioning, autonomous workflow bootstrap, and target diagnostic now fall back to enumerating non-special Win32_UserProfile paths and probing the standard AppData\\Local\\Programs\\Ollama\\ollama.exe location. The executable is still invoked and version-checked; no username or admin elevation is assumed.
- The target Windows diagnostic now executes the production check_autonomous_runner.ps1 under the actual service identity and requires WINDOWS_RUNNER_READINESS_CONTRACT_OK.
- Regression tests assert the profile-discovery fallback in all four entry points.
- Code/test head: ae5f0fb26c26ac02e4a9bd04fd1f8362c9796d6a. Exact-head tests #43211124252, sandbox #43211124343, browser #43211124438, dependency audit #43211124379, and Windows diagnostic #43211116634 were queued/in progress when recorded; focused Codex/context tests have passed, while the full suite and integration gates remain pending.

## 2026-11-12 — Windows Runner service-identity readiness correction

### Root cause
The consolidated PR #722 readiness script still used `Get-Process Runner.Listener`. That check depends on process visibility from the current account and does not prove that the registered GitHub Actions Windows service is running under its configured service identity.

### Correction
- Query `Win32_Service` for a running `actions.runner*` service with a nonzero PID.
- Resolve that PID through `Win32_Process` and require a `Runner.Listener.exe` child whose `ParentProcessId` matches the service PID.
- Report the service name, configured service account, and PID only after both checks succeed.
- Treat CIM inspection failure or an unverifiable listener as not-ready (fail-closed).
- Add a regression test forbidding the old `Get-Process Runner.Listener` path.

### Validation
Current PR head: `ae5f0fb26c26ac02e4a9bd04fd1f8362c9796d6a`. Exact-head tests, sandbox integration, Browser E2E, dependency audit, and self-hosted Windows diagnostic are queued/in progress under runs #43211124252, #43211124343, #43211124438, #43211124379, and #43211116634. None is recorded as passed until its exact-head conclusion is success.

# Engineering Continuity Log

## 2026-11-12 — PR #722: adaptive Codex context bounded by model and host capacity

### Root-cause analysis

- A fixed 64,000-token threshold existed across model selection, settings readiness, PowerShell preflight, Runner checks, and the Codex model catalog. A separate 512-token floor remained in the follow-up implementation, which still contradicted the requirement that Codex adapt to any positive capacity.
- The earlier capacity calculation conflated the hardware-derived token ceiling with the model's advertised context ceiling. It also needed to avoid charging model weights twice when the selected base model was already resident.
- Ollama's verbose model-show response can be unnecessarily large; metadata required by this path is available through verbose=false.

### Corrective changes

- The selected context is clamped to the minimum of the independently reported hardware ceiling, model context limit, and Codex catalog ceiling. A user/workflow request above that ceiling cannot be persisted or passed to Codex.
- Removed both the 64k Codex minimum and the arbitrary 512-token runtime floor. Context validation now accepts any positive integer; zero/negative values and a host budget unable to provide even one token remain invalid.
- Kept the original Ollama model immutable. The workflow creates/reuses a separate -codex-ctx<tokens> variant, verifies its saved num_ctx, confirms the exact allocated context with /api/ps, and repeats the check after Codex.
- Model-weight memory is subtracted only when the base model is not already resident. Hardware capacity is still architecture-aware because KV-cache bytes/token depend on the selected model's architecture.
- Updated readiness/provisioning scripts, Codex catalog validation, dashboard context display, ADR-020, operations guidance, and regression tests. /api/show uses verbose=false; loopback/proxy/redirect and response-size guards remain intact.

### Validation status

- Current branch: fix/autonomous-engineering-show-all-ollama-models-20261112; PR #722 remains Draft and unmerged.
- Target-host diagnostic is configured to test a 10,000-token request on the Windows Runner, including the real Codex tool-call smoke and exact runtime allocation checks.
- Exact-head CI and target-host execution results are pending after the latest commits. No local/target-host success is claimed without captured output.

### Residual risk

- The KV-cache estimate uses the current FP16 assumption and is only a preflight estimate; Ollama's actual loaded allocation and remaining physical-memory check are authoritative.
- Very small positive contexts may be rejected by a particular Ollama/model runtime or Codex build. Such runtime rejection must be reported with evidence; the application must not reintroduce an arbitrary policy floor.
- Smaller contexts reduce the amount of repository and conversation evidence available in one turn. Existing durable checkpoints and bounded-task execution remain necessary.

---

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


## 2026-11-11 — Consolidation of learning implementation branches

- Consolidation target: `feature/unbounded-version-aware-learning-curricula` / PR #710.
- PR #710's branch is a direct descendant of the PR #709 branch: the merge-base equals PR #709 head `e7e2232a7c4e72de7706fc464129e7971793bc6d`; PR #710 contains the full #709 history plus 59 subsequent commits. Thus the research-first curriculum implementation from #709 is included rather than independently reimplemented.
- Audited `feature/learning-gpt-oss-20b-20261110` against the consolidated branch. The consolidated branch already contains the later/evolved model configuration, DB/bootstrap implementation, installer behavior, documentation, and download-resume regression changes. The one missing independent regression artifact, `tests/test_learning_model_pin.py`, was copied into the consolidated branch and is now included in CI.
- Kept the more complete consolidated implementations where both branches changed the same files; did not overwrite later implementations with earlier snapshots. No user learning records or existing schema data were deleted or rewritten.
- A CI failure on the prior head exposed a stale exact-source-URL prompt contract assertion (2 failures; 1,972 passed). The prompt's provenance policy was made explicit about a Sources section and exact source URLs; the next CI run is required to confirm the fix.
- PR #710 remains the single consolidation PR. PR #709 is superseded by #710 and must not be merged independently.


## 2026-11-11 — Exact-head validation completed after model-pin regression fixes (PR #710)

- Updated `tests/test_learning_model_pin.py` to assert the actual fail-closed error contract and to model Ollama's post-pull model list accurately. The test now verifies successful installation/verification of `gpt-oss:20b`, explicit custom-model override, and pull-failure handling without weakening runtime checks.
- Exact tested commit: `5e58301417767124ee01b9f4e331a6fd74354525`.
- GitHub Actions all passed on that exact commit: tests [#43001048500](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048500) — **1,980 passed, 50 deselected**; Windows storage regression job passed; generated learning artifacts, 94 Markdown links, import-cycle checks, deterministic retrieval benchmark, and claim-to-test coverage benchmark (**10/10**) passed. Browser E2E [#43001048937](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048937) passed. Dependency audit [#43001048856](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048856) passed.
- The consolidated PR branch has not been merged. PR #709 is superseded; PR #710 is the single consolidation target. No local target-host or human test was run or represented as passed. Continue other repository engineering work while the mandatory target-host gate remains open; do not merge to `main` until that gate is satisfied.


## 2026-11-11 — Remove arbitrary lesson-length constraints (PR #710)

### Root-cause analysis

- The curriculum had no total topic/lesson/batch ceiling, but the lesson-generation prompt still required 200–260 Persian words and 5–7 paragraphs. Its repair prompt repeated the same cap.
- app/learning_quality.py enforced a 180-word minimum, a 200-word target, and a 260-word maximum. It could reject a detailed lesson for being too long, independent of instructional quality.
- The model was allocated only 1,200 output tokens for initial generation and 1,400 for repair, with a 2,600-token in-memory pipeline ceiling. This was too restrictive for complex but coherent technical lessons.

### Corrective changes

- Prompt versions are now learning-lesson-v3 and learning-lesson-repair-v3; both require topic-appropriate depth, no arbitrary word/paragraph cap, no padding, and explicit decomposition when a topic is too broad for one coherent lesson.
- Removed all word-count minimum/maximum gates from the deterministic quality filter. It continues to evaluate repetition, technical specificity, examples, causal explanation, questions, answers, outcomes, and malformed language.
- Increased each generation/repair output allowance to 3,900 tokens and the two-call in-memory budget to 7,800. The existing durable 9,000-token per-job ceiling remains compatible and leaves 1,000 tokens for optional claim review. Hardware/context limits and fail-closed resource checks are preserved; this does not create an unbounded single inference.
- Added regression coverage for lessons longer than 260 words, length-independent quality decisions, prompt version changes, and the expanded generation/repair token allowances.

### Validation boundary

- Automated CI for the new code and documentation is pending. No live gpt-oss:20b inference, target-host resource measurement, or human quality adjudication is claimed.
- The total course remains unlimited through checkpointed batches; per-call token limits are resource-safety controls, not curriculum-size limits.


## 2026-11-11 — Chronological version learning and automatic release watch

- Extended curriculum and lesson prompts to require independent syllabus decomposition for any requested subject, release-history discovery from the earliest documented release through the latest, and oldest-to-newest teaching order. Version groups include year/date, exact version, and applicable build/edition/compatibility context.
- Added parser validation for ascending release-year/date arrays, real calendar dates, numeric version order, and chronological ordering across version-scoped topics. Prompt registry is now `learning-lesson-v5` / `learning-lesson-repair-v5`.
- Added migration 44 for durable `learning_release_watch` state. Existing researched curricula are seeded for monitoring without rewriting their saved lessons or provenance.
- The learning worker's existing 30-second maintenance cycle processes up to three due watches, queries release notes/history/patch/security/changelog sources, establishes a recurring-query baseline, and checks every six hours. Changed URL/content fingerprints enqueue a deduplicated refresh job; old course paths remain intact and their watches are superseded only after a replacement path is persisted.
- Added regression coverage for release metadata validation, chronology ordering, source-change detection, baseline behavior, migration seeding, and historical path preservation.
- Latest exact-head CI is running on the branch head recorded by PR #710; earlier successful CI does not validate these new changes. No local target-host test is claimed. PR #710 remains unmerged pending the requested local test.


## 2026-11-11 — Configurable LLM providers, temporary switching, and lesson retention

- Added independent provider profiles for chat, research planning, learning, programming, factual review, and embeddings. Supported inference providers are Ollama and OpenAI-compatible Chat Completions; embeddings use the matching OpenAI-compatible `/embeddings` endpoint. This covers local compatible servers (LM Studio, vLLM, llama.cpp server, LocalAI) and hosted endpoints/gateways without hard-coding a vendor SDK.
- Added endpoint validation: HTTPS is required for non-loopback hosts; HTTP is restricted to loopback; URL credentials/query/fragment and redirects are rejected; `localhost` is pinned to `127.0.0.1`. API-key values are never stored in settings; only an environment-variable name is stored, and key header control characters are rejected.
- The dashboard can switch provider, endpoint, model, and key-env name per lane; non-local endpoint selection explicitly discloses that prompt/history/context are sent to that service. Settings can be persisted or applied in-memory until process restart. Existing per-lane model fields remain the storage-compatible model identifiers.
- Routed `ollama.chat`, curriculum generation, lesson generation and repair, claim review, programming, chat/research planning, and semantic embeddings through the selected provider. Remote-provider lesson generation does not fail because the host cannot reserve local RAM for remote model weights. Ollama startup/model pulls are skipped when all active LLM and embedding lanes have migrated away from Ollama.
- Saved settings now remain authoritative over environment defaults after first initialization; environment model values seed a new profile but no longer overwrite an operator's saved UI selection on every restart.
- Non-JSON/free-form model answers are retained as unverified lesson content even if the model also fails the structured assessment schema and the repair attempt is malformed. Quality scores trigger diagnosis/repair and remain attached to the candidate; they do not delete the lesson or source evidence. Low-quality lessons remain `unverified` and staged as `awaiting_test`, not promoted as trusted factual knowledge.
- Added regression tests for compatible-provider routing, local endpoints, API-key environment lookup, embedding order, provider switching, temporary non-persistent settings, optional Ollama startup, and free-form lesson retention.
- Versioned curriculum generation remains independent of learner examples: the prompt requires complete subject taxonomy, release history from earliest documented release to latest, and chronological teaching order; parser validation enforces the order and actual calendar dates.


## 2026-11-11 — PR #710: remove source-count admission gate

- Root cause: _collect_sources required at least three usable pages across two domains and retried indefinitely otherwise. This made source availability a prerequisite for learning, contrary to the requested policy to retain incomplete/unofficial evidence and continue with model knowledge when necessary.
- Change: source count and publisher authority are now provenance/quality signals, not curriculum admission gates. All usable results are retained; zero-source and sparse-source research can proceed. Lesson provenance remains unverified when it is model-only or lacks adequate evidence.
- Prompt: curriculum research now explicitly collects relevant official, unofficial, community, archived, partial, and conflicting material without authority-based exclusion; source type, freshness, corroboration, and uncertainty are classified separately.
- Regression: replaced the old retry-on-one-source test with parameterized zero-source/one-source continuation coverage and added assertions for the prompt's non-exclusion policy.
- Documentation: updated docs/LEARNING.md and docs/PROJECT_STATUS.md to describe the new contract.
- Commits: 1dcdae6ffdab24d1bce47c413f282506b8f0fb94, 3c6eb85c29f84afcb319457ab98164cde162a924, b9b159a53893f0b7a0e0fd7a1f6210f231e6f258, e1d17548514d1d655c8ca5720c1e8c2d432bd2a9, 38fbbbf564be6948f8aa70b25710a696a9486d8e.
- Validation: no exact-head CI result is available yet for the latest commit. Do not infer success from earlier PR runs. Local target-host validation remains a separate pre-merge gate.

- Follow-up: removed the 40-document aggregate cap and three-documents-per-domain cap as well. Every usable result returned by each research query is now retained with provenance; the prompt still selects a bounded representative excerpt set per model call to respect context limits, without deleting the persisted source inventory. Added a regression with four sources from one domain per query.

- Regression found during follow-up call-site audit: the release-watch collector still referenced the removed aggregate source-cap constant. Removed those stale cap checks too; otherwise the first valid release-watch result would raise NameError. Existing release-watch baseline/change/deduplication tests exercise this path. Exact-head CI is the next verification step.

- Second checkpoint audit found another stale source-cap reference in `_valid_checkpoint`, which would have failed when resuming any checkpoint containing source records. Removed the slice and added a regression that recovers 45 source records with query provenance intact. This preserves the entire accumulated source inventory across restart/resume.

- Prompt audit also removed an authoritative-only condition from version-change coverage. Relevant unofficial reports are now collected and attributed alongside official material; unsupported or conflicting claims stay unverified rather than being omitted or promoted. Added a regression preventing the authoritative-only wording from returning.


## 2026-11-11 — Universal learning contract and cross-batch chronology

- Added the universal subject-agnostic learning contract to the linked master engineering prompt branch and copied it into this PR branch. It covers complete subject taxonomy, earliest-to-latest version/year sequencing, evidence provenance/retention, non-blocking assessment, additive automatic correction, release monitoring, and provider/model migration.
- Code audit found a chronology gap: `_parse_outline` validated version order inside each model batch, but `handle_curriculum_task` appended batches without validating the merged course. A later batch could therefore introduce an older release after newer topics.
- Added `_validate_chronological_topics` and invoke it after all batches have been merged and before persistence/lesson queueing. Added regression coverage for individually valid but globally out-of-order batches.
- Commits: prompt `fcece2b2a7aa81a215d32e1b79ccdf7959605d2e`; implementation `28e1917e924962086dc143a4deb58b76312933b3`; regression `377fe87b76a31e61eee03ecb95453f65f3e2bab9`.
- Validation: exact-head CI for the latest implementation commit has not yet returned; no local target-host or GPT-OSS 20B runtime validation is claimed.

- Added `tests/test_master_engineering_prompt_contract.py` to guard the new subject-agnostic learning contract against accidental removal. The assertion wording was checked against the actual prompt text after the first draft exposed a mismatch; the final test now checks the exact phrase used by the prompt.


## 2026-11-11 — Lesson self-audit and semantic assessment prompt v6

- Added a pre-delivery self-audit instruction covering version/date chronology, commands/APIs/code against the stated release, prerequisites, missing steps, contradictions, unsafe side effects, and expected outputs. Detected defects must be corrected before returning; execution must never be claimed without actual execution evidence.
- Added explicit semantic assessment guidance: accept valid paraphrases and alternative correct approaches; distinguish partial correctness from factual error and explain missing concepts rather than relying on exact-string matching.
- Preserved additive correction/history behavior. Bumped the lesson-generation prompt registry to `learning-lesson-v6`; lesson-repair remains `learning-lesson-repair-v5` because its prompt text did not change. Updated registry/self-correction tests and learning documentation.
- Commits: `aae86acb589e159235519821505c805fc8551c5d`, `f94ffc4403b4a39ba8c79dfd6f34efb9117c0bef`, `97d6d11050ef2576c0d0f2c5457ecae943e9e959`, `6fb7808786655024f4242df63fe1ae19ff0f7c21`, `144b7eb6d3d3e54e4f52b22de63f9a3eb45c4e5a`.
- Validation: prompt/registry/test/documentation references were cross-checked. Exact-head CI and target-host execution remain unverified.


## 2026-11-11 — Remaining acceptance gaps identified (not closed)

- The lesson prompt now asks for semantic assessment, but `submit_assessment` still uses deterministic `accepted_phrases` rubric scoring as its progress source. The separate semantic reviewer is opt-in/advisory and does not change score or progress. This does not yet fully satisfy automatic acceptance of semantically correct paraphrases; a safe, auditable integration remains open.
- General research/chat answers are persisted in conversation history, and the separate browser-learning workflow stores ChatGPT lesson outputs as unverified lessons. General chat/research responses are not yet automatically ingested as subject-scoped learning candidates for later curriculum correction. This remains open; do not claim the prompt change alone implemented it.


### 2026-11-11 — اصلاح خطای indentation در اعتبارسنجی ترتیب سرفصل‌ها

- CI دقیق SHA قبلی `9b5b1731e39fbc8d1500f7b0b83ad72f1cf14841` شکست را نشان داد: `app/learning_curriculum_tasks.py:689` به‌دلیل خروج ناخواستهٔ validator ترتیب نسخه‌ها از بلوک `with` دارای `IndentationError` بود. همین خطا باعث شکست collection در ۲۹ ماژول تست و شکست تست Windows storage شد.
- فراخوانی `_validate_chronological_topics` و ساخت مجموعهٔ URL منابع دوباره داخل همان بلوک تراکنش قرار گرفتند؛ metadata همان متغیر `topic_metadata` را استفاده می‌کند. تغییر کد در commit `ff84957543644d74a04169a1bf13b57f6f6c1b8b` ثبت شد.
- روی SHA جدید، workflowهای tests و browser-e2e در حال اجرا هستند؛ تا پایان آن‌ها نتیجهٔ موفق اعلام نمی‌شود. شکست browser-e2e در SHA قبلی در مرحلهٔ نصب Chromium و به‌علت DNS ناموفق مخازن Ubuntu بود، پیش از اجرای smoke test؛ این شکست به‌عنوان خطای برنامه طبقه‌بندی نمی‌شود.


## PR #710 — semantic assessment execution and chat-to-learning ingestion

- Exact-head CI audit found that lesson prompt v6 requested semantic equivalence but `submit_assessment` still persisted only deterministic phrase/rubric scoring. The assessment path now saves the deterministic attempt first, closes the database transaction, then invokes the configured independent semantic reviewer. Only an all-`matches` result can promote that attempt to 100/completed; partial, incorrect, uncertain, unavailable, malformed, or identity-invalid reviews retain the deterministic baseline. The final score, score provenance details, path progress and idempotent replay are regression-tested. This is model-assisted grading, not factual certification.
- General assistant answers persisted through `append_assistant_message` are now staged locally as `learning_chat_candidates` under migration 45. Candidates retain the query hint, conversation/message/task identifiers, SHA-256 hashes and provenance, begin as `unverified`, and are never automatically promoted to trusted factual knowledge or sent to an external provider. Very short answers and obvious credential-bearing exchanges are skipped. The candidate capture is best-effort after chat persistence, so staging failure does not discard a saved conversation.
- The subject hint is the originating user query, not yet a canonical taxonomy label; candidate-to-course promotion remains a separate workflow. The ingestion path covers assistant messages persisted in local chat history, not transient responses that are never stored.
- Exact-head CI on `e62342e79d882c44e07340e28a728bcbf03846ab`: main suite **2028 passed / 50 deselected**; Windows storage **11 passed / 47 deselected**; browser E2E and dependency audit succeeded; generated learning artifacts, Markdown links, import-cycle guard, retrieval benchmark and claim-to-test coverage benchmark all succeeded. The claim-to-test benchmark passed 10/10 with zero false accepts and zero false rejects.
- Target-host execution and a live `gpt-oss:20b` benchmark were intentionally excluded from the merge gate at the user's direction and are not claimed as completed.


## Universal learning contract v2 — source coverage and delivery policy

- Branch: `feature/universal-learning-contract-v2`, based on post-PR-710 main commit `9e198a0640593bcc3892f3a97aa8956ba522653c`.
- Master prompt strengthened with subject-map and release-ledger requirements, exact per-lesson/per-claim source and applicability provenance, true release-date ordering (not lexical version ordering), resumable evidence processing with no total source/version/year/lesson caps, release monitoring and additive correction revisions, and persistent/temporary provider/model selection acceptance.
- Prompt merge policy now distinguishes reproducible exact-SHA automated gates from owner-local, target-host, physical-device, and human acceptance. Unavailable tests remain NOT RUN/PENDING and are not silently passed; they do not block routine code delivery by default. Repository-enforced checks and independent security approval are not bypassed.
- Curriculum source delivery changed from repeatedly sending the same first eight sources to round-robin source batches. The task forces continuation until all retained source records have been presented to the learning model, even if the model prematurely returns `has_more=false`. Evidence-only continuation batches can complete without inventing duplicate topic titles.
- Removed fixed caps on topic source URL count, version/year/date array count, and coverage-area count. The JSON response and prompt budgets remain per-request safety bounds; the full curriculum remains checkpointed and resumable.
- Initial curriculum research now requests up to 30 fetched pages per query request. Release-watch polling retains its previous five-result page to preserve existing source-fingerprint semantics; its query set now also covers documentation errata, compatibility changes, security CVEs, deprecations, removals, and EOL. Neither per-query page size caps the total course.
- Added regression tests for 103-source round-robin pagination, complete review of 17 sources across three batches despite premature model completion, metadata arrays with 45 releases/source URLs, and web retrieval above five pages.
- Exact-head CI has not yet run for this branch. The tests and implementation are pending automated verification; no local/target-host/human test is claimed. Remaining known gaps include a durable canonical subject/release coverage ledger, automated candidate-to-course promotion, lesson correction/revision workflow, and verification of all provider-role settings/temporary overrides.



### Full-source retention and publication-date provenance

- Added learning schema migration 46, `complete-curriculum-source-body-retention`, with `learning_research_source_text`. Full fetched page text is stored once per task/source hash and linked to the resulting immutable research path; repeated task checkpoints and `learning_path_research.sources_json` retain only source metadata/excerpts.
- Curriculum research now rehydrates complete bodies from the source-text table. Legacy checkpoints with excerpt-only evidence attempt to re-fetch their exact known HTTPS URLs; if unavailable, the excerpt remains explicitly incomplete and is never represented as a complete page.
- Each full source body is split into ordered 1,000-character evidence chunks. Chunks are interleaved across research query angles and source records, and the existing source-batch continuation now covers every chunk before the model may finish the curriculum.
- HTML metadata extraction records common publication and modification fields (`article:published_time`, `datePublished`, `DC.date.issued`, `article:modified_time`, `dateModified`, `last-modified`, and `time[itemprop]`). Unknown dates remain null; retrieval timestamps are stored separately.
- Migration tests now assert schema version 46 and source-body table structure; curriculum tests cover full-body persistence/chunk order; web-source tests cover publication/update metadata. Exact-head CI is pending.
- The web retrieval page remains capped at 30 results per query request by provider/request safety limits; the number of queries, source bodies, source chunks, and total course batches is not capped by that page size.



### Release chronology validator refinement

- Curriculum chronology now uses the recorded release date first, then release year, and product-specific version order when dates are unavailable. This avoids relying only on lexical version labels and supports prerelease ordering before stable releases.
- Added regressions for release dates that conflict with otherwise ascending version labels and for release-candidate versions sorting before the matching stable release.
- These changes are awaiting exact-head CI.



## Current-state correction — PR #710 merged; PR #711 is the active learning continuation

- Any earlier dated entry in this log stating that PR #710 was open, Draft, or waiting for local/target-host testing describes a historical snapshot and is superseded. PR #710 merged into `main` on 2026-11-11 as `9e198a0640593bcc3892f3a97aa8956ba522653c`.
- The current follow-up is PR #711, branch `feature/universal-learning-contract-v2`. Its exact head and CI status must be read from live GitHub; no earlier SHA or test result applies to it.
- The source-body retention and common HTML publication/modified-date extraction work is implemented in this follow-up with migration 46. It must not continue to be listed as an outstanding gap once its exact-head CI passes.
- Historical note: the release watch previously polled five results per query to preserve the initial fingerprint baseline. PR #714 expands each recurring query to the maximum supported search page (30 results); existing watches may therefore enqueue one intentional additive refresh as their baseline expands. This increases coverage but does not claim exhaustive internet-wide detection.



## 2026-11-12 — Non-destructive learning admission and cursor-based chat evidence

- Clarified the master engineering contract: data retention/admission is independent from source authority, lesson completeness, test pass, semantic-review verdict, and learner mastery. Low-confidence or malformed material remains preserved with provenance/status; assessment is not a deletion gate.
- Extended chat-observation retrieval with a stable candidate-ID/content-offset cursor. Search now matches both the original user query and answer content, and long answers are delivered in bounded chunks without losing their remaining text.
- Curriculum synthesis checkpoints the cursor and continues across additional batches until all matching prior assistant observations have been delivered. This is separate from external source pagination and does not promote observations to trusted facts.
- Added regression coverage for content-only topic matches and reconstructing all chat observations from bounded pages.
- Local test execution is unavailable in this editing session; exact-head GitHub CI is required before merge. No local/target-host/model runtime pass is claimed.


## 2026-11-12 — Wider automatic release-watch discovery

- Increased each recurring release-watch query from five results to the full 30-result page supported by the public-source discovery adapter.
- Added a regression where the changed release note appears after the first five results; the watch must detect it and enqueue a versioned curriculum refresh.
- Existing watch fingerprints may trigger a one-time additive refresh after this coverage expansion. Historical curricula and lessons remain unchanged.
- The 30-result limit is the search adapter's per-query page bound, not a total curriculum/source-retention cap; exhaustive detection across all public websites is not guaranteed by search alone.

## 2026-11-12 — Autonomous Engineering ownership, recovery, and modularization

- Reused the existing ownership/recovery implementation from the earlier unmerged PR #707 and ported it onto the current main baseline `461c0a2b1deaf5f8af8486846f167da8185c580a`, preserving the concurrently merged release-watch improvements.
- Kept ownership boundaries explicit: repository worktree execution lock, checkpoint-watcher lifetime lock, watcher process identity, and durable task/PR state mutex are separate modules with separate responsibilities.
- Durable manual-task and PR state transitions are serialized across Python and PowerShell. The local state directory uses one shared path contract, and sequential local-state operations are pinned to the unique `autonomous-state-owner` Windows Runner label.
- Checkpoint metadata binds readiness to run/task/base SHA and process identity. Ambiguous process identity, access-denied inspection, failed termination/reaping, malformed metadata, and patch-size mismatch fail closed and preserve existing state.
- Fixed pending PR URL validation: the prior raw regular expression escaped the dot twice, rejecting valid GitHub URLs. Validation now parses the URL, enforces HTTPS and the GitHub host, rejects credentials/query/fragment, validates path segments, and requires the path PR number to match the durable record.
- Updated workflow contract tests to reflect the concurrency-safe design: manual task persistence executes inside the serialized controller job on the single state-owning Runner, rather than in an independent pre-gate job.
- Renamed exported PowerShell functions to approved verbs. The previous module import warning polluted stdout and caused the cross-language SHA-256 equality test to fail despite the digest being correct.
- Extracted GitHub REST/GraphQL transport and exact-SHA workflow polling into `autonomous_engineering_github.py`.
- Extracted PR discovery, validation, owner-review handoff, post-merge verification, and durable pending-PR publication into `autonomous_engineering_pull_requests.py`. The cycle controller retains its public CLI and compatibility functions and injects dependencies; the new modules do not import the controller.
- Exact-head CI on the corrected architecture head has not yet been rerun. Prior heads demonstrated passing Windows storage/recovery, Browser E2E and dependency audit, but Linux failed on stale static assertions that have been corrected. No green result is inferred from a prior SHA.
- The live Codex/Ollama run remains a separate runtime blocker: the previous target-host run failed because Codex CLI rejected a reserved built-in `model_providers.ollama` override. It must be fixed and tested before claiming live Autonomous Engineering operation.
- Target-host crash/restart, soak, staging restore/rollback, and owner acceptance remain external acceptance gates. No automatic deployment, destructive state cleanup, or user-data migration is introduced.

## 2026-11-12 — Codex/Ollama runtime and context validation

- Removed the implicit `gpt-oss:20b` fallback. The dashboard lists installed original Ollama models, excludes generated context variants, and saves only a model that exposes an architecture-specific context limit of at least 64k tokens and the `tools` capability.
- Added hardware-aware context selection from model KV-cache metadata, installed model size, and measured free physical RAM. The selector fails closed on missing metadata or insufficient headroom and persists a context-specific model variant without mutating the original model.
- Replaced the rejected reserved built-in provider override with the documented custom provider ID `ollama-local`, configured for the local Ollama Responses endpoint and no authentication. Timeout and retry settings now belong to this unique custom provider ID.
- Fixed the PowerShell-to-Python context bridge by writing JSON to an explicit temporary file and invoking `app/ollama_context.py --input-file`; the previous native stdin pipeline could reach Python as empty input and raise `Expecting value: line 1 column 1`. The temporary file is removed in a `finally` block.
- Added model metadata, context selection, explicit-file bridge, Codex provider, settings API, and browser UI regressions. Exact-head CI and the real Windows Codex/Ollama diagnostic remain pending.

- The first exact-head Linux focused run exposed two test-fixture issues: the low-memory case failed earlier than the intended 64k-capacity gate, and one metadata test accessed persistent settings without isolating its database dependencies. The test now uses a 10 GiB boundary case and mocks settings/hardware explicitly. A fresh exact-head CI run is still required.


## 2026-11-12 — PR #719 exact-head integration corrections

- Active implementation: PR [#719](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/719), branch `feat/autonomous-engineering-complete-final-20261112`, based on main `461c0a2b1deaf5f8af8486846f167da8185c580a`.
- The first combined runtime head `581ba179b2303a88bd2a1a8737479e38946aeeb6` exposed regressions in the shared LLM adapter: provider-routing helpers and model identity had been removed, and `chat()` no longer accepted output/context overrides. Follow-up commits restored OpenAI-compatible chat/embedding routing, provider-scoped model identity, direct learning-lane routing, and the bounded `num_predict`/`num_ctx_override` contract. The Codex regression was aligned with the documented custom provider ID `ollama-local`; the reserved built-in `ollama` namespace remains untouched.
- A subsequent focused checkpoint test exposed a real refactor defect in `scripts/autonomous_engineering_cycle_checkpoints.py`: the extracted watcher function referenced module globals (`os_name`, `sys`, `subprocess`, `ROOT`) instead of its injected dependencies. The watcher now uses `os_module.name`, `sys_module`, `subprocess_module`, and the injected `root`; `restore_checkpoint` retains its own `os_name` argument. The focused regression suite must pass on the new exact head before acceptance.
- Issue [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290) explicitly prohibits automatic PR merge. The previous sentence in section 9 of `MASTER_ENGINEERING_PROMPT.md` that pre-authorized autonomous merge conflicted with that constraint; it has been replaced with owner-only manual merge and exact post-merge verification. Regression tests guard the UI and operations guide against reintroducing automatic-merge claims.
- Corrected user-facing page and operations documentation that incorrectly described automatic PR merging. The controller leaves validated PRs open for owner review, never calls the PR merge API, and advances only after the owner manually merges and exact post-merge CI succeeds. Manual task persistence documentation now matches the serialized concurrency-slot implementation.
- Exact-head CI snapshot on `92db74af655253f90caf7747c9ec2b87ed76ef9e`: focused checkpoint ownership/concurrency tests passed; Windows Storage (including real watcher start/stop/recovery, PowerShell-to-Python context bridge, fault injection, lock exclusion, PowerShell parsing, and SQLite restore/migration checks) passed; Browser E2E passed; dependency audit passed. The full Python suite was still running at the time of this snapshot. Subsequent prompt/status/log edits require fresh exact-head CI. No target-host Codex/Ollama tool-call smoke, soak, crash/restart acceptance, or staging restore/rollback is claimed.
- The full-suite run on that SHA subsequently completed with **2,127 passed, 2 skipped, 51 deselected, 7 failed**. Five failures shared a callable-shadowing defect in `ollama_status` (the injected context-variant predicate was overwritten by a bool and then used again); one dashboard test referenced documentation variables declared in a different test; one Codex test still expected the removed `--oss` arguments instead of the current custom-provider config. These were corrected in the follow-up commits; exact-head CI must verify them.
- No direct main write, auto-merge, deployment, destructive state reset, or user-data deletion was performed. Independent security review and target-host acceptance remain open.


## 2026-11-12 — PR #719 exact-head verification complete

- Exact tested head: `1528b86cdc0c0f70a17be2434e374ec7ede2c61c`, based on main `461c0a2b1deaf5f8af8486846f167da8185c580a`.
- Focused checkpoint/context/provider suite: **11 passed**.
- Full Python suite: **2,135 passed, 2 skipped, 51 deselected** in 226.49 seconds. No failures.
- Windows Storage regressions: **passed**, including explicit PowerShell-to-Python context-file bridge, real watcher start/stop/recovery, checkpoint fault injection, cross-process repository lock exclusion, PowerShell parsing, and SQLite migration/restore/staging cleanup.
- Browser E2E: **passed**. Dependency audit: **passed**. Deterministic lexical retrieval benchmark: **passed**. Fail-closed claim-to-test coverage benchmark: **passed**.
- Follow-up defects closed before this run: preserve the injected context-variant predicate in `ollama_status`; import `OllamaModelMetadataError` in the service module; place documentation fixtures in the correct dashboard test; align Codex argument tests with the custom `ollama-local` provider; include a human `merged_by` actor in the accepted-merge fixture; reject bot merge actors before post-merge validation or state cleanup.
- Issue #290 remains authoritative: no automatic PR merge, deployment, cloud provisioning, or remote execution. The controller never calls the merge API and accepts only a human merge actor; owner review and manual merge remain required.
- This CI proves repository tests and the configured Windows storage regression job only. A real target-host Codex/Ollama tool-call smoke, long-duration soak, crash/restart acceptance, staging restore/rollback, and independent security review (Issue #52) remain pending and are not claimed.
- No direct write to `main`, force-push, deployment, destructive state reset, or user-data deletion occurred. This verification snapshot is for the exact SHA above; any subsequent commit requires its own exact-head CI.

## 2026-11-12 — Adaptive Codex context bounded by model and hardware capacity (PR #722)

### Root cause

- A fixed 64,000-token floor was duplicated across the context selector, Autonomous Engineering settings/readiness, Windows preflight and provisioning scripts, Codex catalog validation, dashboard copy, and operations documentation.
- The prior selector also folded the model maximum into the reported hardware maximum, obscuring the two independent constraints.
- The service and PowerShell metadata paths used verbose Ollama /api/show responses even though the concise response exposes the architecture metadata needed for context planning.

### Decision and implementation

- Recorded ADR-020. Hardware capacity is computed independently from model capacity using available physical RAM, the 4 GiB reserve, installed model weights, runtime overhead, and architecture-specific KV-cache bytes/token.
- The effective context ceiling is min(hardware capacity, model maximum, Codex catalog maximum). The selected context is the requested value clamped to that ceiling, or the effective ceiling when no request is supplied.
- Removed the Codex-specific 64k minimum. The 512-token floor remains only as the minimum valid model/runtime metadata window; below that, safe initialization cannot be established.
- Updated the Python selector, Autonomous Engineering status/settings, PowerShell preflight, Codex catalog, readiness/provisioning checks, dashboard wording, and operations guide. User/workflow requests above the verified ceiling are clamped before model-variant creation and Codex dispatch.
- Switched relevant Ollama /api/show metadata requests to verbose=false while preserving response-size guards and runtime verification through /api/ps.
- Added regressions for 10,000-token hardware capacity, an 8,192-token model cap, request clamping, and saving an installed model below 64k.

### Validation boundary

- Active branch: fix/autonomous-engineering-show-all-ollama-models-20261112; existing PR: #722.
- Exact-head CI for the new commits has not yet completed. No local test output, target-host Codex run, or real tool-call smoke is claimed.
- Runtime acceptance must prove the Codex model catalog carries the selected value and Ollama /api/ps reports exactly that allocation before and after Codex.
- No direct main write, force-push, automatic PR merge, deployment, or destructive state change was performed.

## 2026-11-12 — PR #722 exact-head GitHub CI and contract correction

- Verified exact PR head `ff74389cfe033afc9ccf1c9d377930a1cf634a3e` against GitHub Actions. The full Python suite completed with **2,146 passed, 2 skipped, 51 deselected** in 248.97 seconds; Windows Storage regressions passed; Browser E2E passed; dependency audit passed.
- The claim-to-test coverage benchmark completed with **10/10 cases passed**. Markdown link validation passed for 96 files.
- Corrected a documentation inconsistency: ADR-020 and the implementation permit every positive integer context, including one token when both model metadata and runtime allocation support it. There is no 512-token or other fixed minimum. Zero/negative values, missing metadata, and a runtime allocation mismatch remain fail-closed.
- Updated `docs/ROADMAP_ACCEPTANCE.md` and `docs/PROJECT_STATUS.md` to reflect the actual contract and exact-head evidence. Historical entries are retained; this entry explicitly supersedes their stale 512-token-floor and pending-CI statements.
- The separate self-hosted Windows diagnostic has passed its exact-head CI wait and service-account preparation, and is currently running the adaptive-context regression step. Installed-model inventory, actual Ollama `/api/ps` allocation, Codex `exec_command` tool-call smoke, and post-smoke allocation verification are not yet evidenced; they remain pending and are not claimed as passed.
- No direct `main` write, force-push, automatic merge, deployment, or destructive state reset was performed. The PR remains Draft/open. GitHub-side implementation and CI work proceeds independently of any later human acceptance; the repository's explicit target-host and owner/manual-merge safeguards remain unchanged.


## 2026-11-12 — PR #722 exact-head and target Windows runtime acceptance completed

- Exact PR head `326b41a862661f032143fb0b8d26f80d8174ffc5` completed all required GitHub workflows successfully: tests **2,146 passed, 2 skipped, 51 deselected**; Windows Storage regressions, Browser E2E, and dependency audit passed.
- Self-hosted Windows diagnostic [#43197193053](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43197193053) passed. It enumerated 13 installed Ollama models with no metadata errors; verified `gpt-oss:20b-codex-ctx10000` with architecture maximum 131,072 and actual runtime allocation 10,000; measured 10,666,065,920 bytes of remaining physical RAM after allocation; executed a real Codex `exec_command` JSONL smoke with exit code 0 and verified the workspace probe, paired command events, expected agent message, and completed non-failed turn; re-verified the allocation after Codex and released the internal model from RAM.
- Updated the PR description and acceptance records to distinguish exact-head CI and target-runner evidence from the still-open project-wide soak, crash/restart, staging restore/rollback, independent security review, and human evaluation gates.
- No direct `main` write, force-push, automatic merge, deployment, or destructive state reset was performed. PR #722 remains Draft/open for owner review.


## 2026-11-12 — Target-host compatibility and Docker failure correction (PR #722)

- Re-audited the module against the target profile: Intel Core i5-12400 (6C/12T), 31.78 GiB-class physical RAM, Intel UHD Graphics 730, Windows service-account runner, Ollama 0.40.16, and pinned Codex CLI 0.167.0.
- Removed the diagnostic's branch-specific 10,000-token default so an empty request exercises automatic model/hardware/Codex capacity selection. The previous 10k smoke remains a manual-request test only.
- Removed hard-coded Admin-user Ollama paths and PowerShell npm/Codex wrappers; use npm.cmd/codex.cmd and locate ollama.exe from PATH or its active process.
- Added target hardware fingerprint and /api/ps device-allocation evidence. WMI AdapterRAM for Intel UHD is explicitly not treated as dedicated VRAM.
- Integrated native process identity/termination and optional psutil telemetry. Windows process creation time is rechecked on the opened handle; Linux pidfd pins the process object and prevents PID-reuse signaling. Unsupported safe termination fails closed.
- Repaired isolated Docker behavior tests: resolve/validate the Git HEAD on the host and pass only the SHA into the no-network, read-only test container. The container does not receive a .git mount.
- Added native target-host architecture acceptance for telemetry, locks, watcher identity, task-owner preservation, and pending-PR cleanup.
- PR #720 and PR #721 were closed as superseded; their relevant changes were reconciled into PR #722 without importing the old 64k context floor or verbose metadata behavior.
- Exact current head 1bb8a4f802d2496e355dd4d86a0595d275512cad has five workflows queued/running (tests, sandbox integration, browser E2E, dependency audit, Windows diagnostic). No pass is claimed before completion.
- No merge, deployment, direct main write, force-push, or destructive user-state operation was performed.


- Scope clarification: the removal of the fixed 64k/512-token floors applies to the Codex adaptive-context path. The separate general Ollama chat runtime retains its existing 512-token minimum; it is not used to clamp or reject Codex context selection.


## 2026-11-12 — Windows Runner Python-version contract correction

- **Root cause:** the autonomous Windows workflow hard-coded `C:\\Program Files\\Python311\\python.exe`, while Runner provisioning and the operations contract explicitly accept Python 3.11 or newer. A valid system-wide Python 3.12+ installation could therefore pass provisioning but fail at workflow startup.
- **Correction:** resolve `python.exe` from the actual Runner service account's PATH, validate its executable path, and enforce the same Python >=3.11 contract before adding its directory and Scripts directory to the workflow PATH. No Python installation, user profile, or machine-specific path is assumed.
- **Regression protection:** the target-host diagnostic contract test now requires version-aware Python discovery and rejects reintroduction of the Python311 hard-coded path.
- **Scope/safety:** this changes only runtime discovery; it does not install software, alter the user's machine, weaken service identity checks, or change Ollama/Codex context selection.
- **Validation:** exact-head GitHub Actions are required for the new commit; results are pending and will be recorded against the exact SHA. The PR remains Draft and is not merged.


## 2026-11-12 — Clarify upstream Codex context recommendation

- **Evidence:** the official Ollama Codex integration guide recommends a context window of at least 64k tokens. The adaptive-context implementation intentionally removes an application-enforced 64k floor so constrained hosts can attempt smaller verified contexts; those are not equivalent to the upstream-recommended configuration.
- **Correction:** ADR-020, the operations guide, and README now distinguish the application's no-fixed-floor policy from Ollama's >=64k recommendation. Sub-64k operation is explicitly labelled best-effort and requires a successful real Codex tool-call smoke test; /api/ps allocation alone is not presented as proof that arbitrary prompts fit.
- **Regression protection:** documentation-contract tests assert that the upstream recommendation and best-effort qualification remain visible.
- **Source:** https://docs.ollama.com/integrations/codex
- **Validation:** exact-head GitHub Actions are running for the resulting commit; no result is claimed until all required checks complete.


## 2026-11-12 — Documentation regression assertion repair

- The first exact-head run after the upstream recommendation clarification exposed a documentation-only assertion mismatch: the README sentence was rephrased so the existing contract test no longer found its required stable phrase. The implementation and behavior were unaffected.
- Restored the established wording while retaining the new Ollama >=64k best-effort compatibility qualification. The assertion was not removed or weakened; the full exact-head workflow matrix is rerunning.


- The next exact-head run found a second stable-wording assertion in the operations guide. The guide now preserves the existing contract phrase while keeping the new upstream >=64k recommendation caveat. No assertion was removed or weakened; another exact-head run is required.


## 2026-11-12 — Windows checkpoint watcher CIM startup hardening

- **Observed failure:** exact-head Windows Storage regression failed in `test_checkpoint_watcher_real_process_publishes_run_scoped_readiness`. The watcher reported that its Windows process metadata query could not complete. The failure occurred in the subprocess invocation boundary before process identity could be published; the prior wrapper discarded the underlying `OSError` / subprocess exception, so the precise host-side cause (executable resolution versus startup timeout) was not observable from CI evidence.
- **Correction:** resolve Windows PowerShell from the OS `SystemRoot` / `WINDIR` installation path first, retaining PATH fallback; increase the bounded CIM subprocess timeout from 8 to 20 seconds; include the exception type and message in fail-closed diagnostics. The test's outer readiness window is 45 seconds so it can accommodate the bounded query.
- **Regression protection:** added a Windows process-snapshot test proving system PowerShell resolution, bounded timeout, returned PID/creation-time/command parsing, and no relaxation of ownership validation.
- **Safety:** CIM failure still fails closed. PID, creation-time, command-line, run/task/base-SHA checks and the native Windows handle/GetProcessTimes recheck are unchanged. No process is terminated based on a failed metadata query.
- **Validation:** new exact-head GitHub Actions are required; previous green jobs on earlier SHAs are not reused as evidence for this change.


## 2026-11-12 — PowerShell 5.1 bridge and Codex context override hardening

- **Compatibility gap:** the Windows Storage workflow exercised the Python context helper under PowerShell 7 only. The production autonomous workflow invokes its PowerShell preflight under Windows PowerShell 5.1, so the older shell's UTF-8 file/process bridge was not independently covered.
- **Correction:** added a dedicated Windows PowerShell 5.1 integration script using explicit UTF-8-without-BOM input/output/error files and a bounded child process. It covers normal adaptive selection, a supported 1,000-token context below the Ollama 64k recommendation, insufficient-RAM fail-closed behavior, and correct accounting when model weights are already resident. The Windows Storage job now runs this script under `shell: powershell`; a source-contract regression ensures the job cannot silently drop it.
- **Context pinning issue:** Codex supports a global `model_context_window` configuration override, and model catalog entries have a separate `max_context_window` field used to cap such overrides. Supplying only `context_window` in the generated catalog did not prove that inherited Runner-account Codex settings could not raise the context above Ollama's verified allocation.
- **Correction:** generated catalog entries now set both `context_window` and `max_context_window` to the verified allocation, and the CLI passes `model_context_window` explicitly with that same value. Added regression tests for a 1,000-token allocation and documentation coverage. This preserves the upstream >=64k recommendation as a best-effort qualification, not a hard floor.
- **Sources:** [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) and [Codex model metadata schema](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs).
- **Validation:** the Windows Storage regression passed on intermediate code SHA `4643710b92ba782eeaf7213039640c99bf2cc71a` after the CIM hardening. The newly added PowerShell 5.1 bridge and Codex override changes require a fresh exact-head CI matrix; no previous SHA is substituted for that validation.


## 2026-11-12 — Legacy Codex context-variant migration safety

- **Compatibility gap:** the previous fixed-context integration created installed Ollama models named `<base>-codex-64k`; the adaptive integration now uses `<base>-codex-ctx<tokens>`. The model selector and preflight recognized only the new suffix, so a leftover legacy model could appear as a user-selectable base model or remain resident while the next hardware budget was calculated.
- **Correction:** both current and legacy generated suffixes are now classified as internal, excluded from selectable models, and rejected if passed as the saved base model. Preflight unloads both suffix formats for the selected base before recalculating current free RAM and creating the new exact-context variant.
- **Regression protection:** inventory tests include both current and legacy variants; settings reject a legacy variant before Ollama/GitHub access; PowerShell contract tests assert legacy cleanup and rejection.
- **Scope:** the separate general Ollama chat context path is unchanged; PR #706 remains separate.


## 2026-11-12 — Windows PowerShell resolution for hardware and readiness telemetry

- **Compatibility gap:** the GPU telemetry fallback used a PATH-only `powershell.exe` lookup with a 4-second timeout; the psutil-free Runner status fallback used the same PATH-only lookup with a 5-second timeout. On a Windows service account or cold hosted runner, either could report missing GPU/Runner telemetry despite inbox Windows PowerShell being installed.
- **Correction:** added a shared SystemRoot/WINDIR-based Windows PowerShell resolver with PATH fallback. GPU telemetry and the psutil-free Runner status probe now use the resolved executable with a bounded 20-second timeout. Missing/failed telemetry remains unknown/not-ready; it is never fabricated as zero or success.
- **Regression protection:** tests verify SystemRoot resolution, the 20-second GPU probe bound, and service-account Runner detection through the resolved PowerShell path.


- The production PowerShell preflight was aligned with the tested bridge: it now resolves python.exe explicitly, starts the helper with Start-Process, redirects stdout/stderr to bounded temporary files, parses UTF-8 JSON, and removes all temporary artifacts in finally. The PowerShell 5.1 integration test exercises the same Start-Process/file contract rather than a separate native-stdin path.


## 2026-11-12 — Remove the arbitrary 1,048,576-token Codex catalog ceiling

- **Finding:** the adaptive selector's catalog cap was hard-coded to 1,048,576 even though Codex's model metadata schema represents both context_window and max_context_window as signed 64-bit integers. This silently imposed an application limit unrelated to the selected Ollama model or actual host capacity.
- **Correction:** the selector now uses a Codex-safe signed 64-bit ceiling of floor((2^63 - 1) / 9), because Codex derives automatic compaction with (context_window * 9) / 10 using i64 arithmetic. The selected context is still bounded by model architecture and measured host memory. The generated model catalog separately pins context_window and max_context_window to the exact selected runtime allocation, so this representational ceiling cannot authorize an oversized Codex context.
- **Regression protection:** tests now prove a model context of 2,097,152 is not clipped to 1,048,576 and verify the i64 catalog ceiling. General Ollama chat context caps in app/main.py, app/ollama.py, and its UI remain unchanged and separate.
- **Source:** Codex ModelInfo defines context_window and max_context_window as optional i64 fields: https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs


## 2026-11-12 — Repair malformed regression-test assertion found by CI

- **Observed failure:** the exact-head tests and Docker sandbox jobs both stopped during pytest collection with a SyntaxError in tests/test_codex_smoke_diagnostics.py. The error was introduced while updating a PowerShell regex source-contract assertion; the assertion string was truncated and the remainder of its test function was displaced. This was a test-source defect, not a runtime failure in Ollama or Codex.
- **Correction:** restored the entire context-preflight contract test, retained its runner-provisioning assertions, and added the current/legacy variant, Start-Process, explicit python.exe, and UTF-8 stdout/stderr bridge assertions as complete Python statements.
- **Validation:** exact-head tests, Windows Storage, Docker sandbox, browser E2E, and audit must rerun on the new commit; the failed pre-fix runs are not counted as evidence.


- **Arithmetic refinement:** Codex's source uses i64 for context metadata but calculates the default compaction threshold with `(context_window * 9) / 10` (non-saturating multiplication). The safe selector ceiling is therefore `floor((2^63 - 1) / 9)`, not i64::MAX. The model/hardware ceilings bind far below this on supported PCs, but the selector now respects the downstream arithmetic contract instead of merely the field type.


- **Second CI finding:** after restoring the truncated test function, the next pytest collection still found an unmatched closing parenthesis at the end of tests/test_codex_smoke_diagnostics.py. The earlier edit had left a duplicate trailing copy of the pre-existing diagnostics tests after the newly added bridge/config tests. Removed that duplicate tail while preserving the original tests and both new tests; the file now contains a single definition of each test. The failed 1e836 run is not counted; a fresh exact-head run is required.


## 2026-11-12 — Restore single-copy Windows preflight after regression review

- **Observed defect:** the latest Windows-preflight edits left duplicated sections in scripts/verify_ollama_context.ps1. The resulting script had six VerifyOnly branches instead of the original two, and CI correctly caught the drift through the existing source-contract test.
- **Correction:** restored the file from the last verified 375-line revision, then reapplied only the required changes: legacy/current variant rejection and cleanup plus the Windows PowerShell 5.1 Start-Process UTF-8 file bridge. The resulting script is 393 lines and has exactly two VerifyOnly branches, one verification result marker, and one release marker.
- **Regression protection:** the test now checks the internal-variant pattern variable, legacy cleanup, production Start-Process bridge, and the existing exact branch-count contract. The Windows workflow path assertion matches the actual PowerShell command form.
- **Validation:** the failing CI assertions on the earlier script revision are not reused; the new exact-head test and Windows Storage jobs must pass.


## 2026-11-12 — Correct sorted model-inventory regression expectation

- **Observed failure:** the isolated Docker suite found one assertion mismatch in the legacy-variant inventory test. The API intentionally sorts the complete installed-model inventory; the fixture listed the new adaptive suffix before the legacy suffix, while the actual sorted result places `-codex-64k` first.
- **Correction:** reordered only the expected test list to match the API's stable sorted contract. The implementation continues to preserve both installed models in inventory while excluding both from selectable base models.
- **Validation:** fresh exact-head tests and Docker sandbox runs are required; the failed c02c run is not counted as green.


## 2026-11-12 — Correct target-host integrated GPU memory documentation

- **Observed mismatch:** the README still described the Intel integrated GPU as having 128 MB reported memory, while the current target-host record reports Intel UHD Graphics 730, WMI AdapterRAM 2 GiB, and Intel driver 32.0.101.6129. Neither WMI AdapterRAM nor a shared-memory limit is proof of dedicated VRAM.
- **Correction:** README now records the actual 2 GiB WMI AdapterRAM report and driver version while explicitly distinguishing it from dedicated VRAM. Dedicated and shared memory remain separate WDDM measurements; actual model offload remains based on Ollama `/api/ps` `size_vram`, not the WMI field.
- **Regression protection:** target-diagnostic contract test now rejects the stale 128 MB statement and requires the recorded AdapterRAM and driver details.
- **Scope:** no GPU offload is assumed or forced. The Intel UHD 730 remains an integrated adapter; CPU fallback on the i5-12400 remains valid and observable.


## 2026-11-12 — Target-host gpt-oss context-capacity regression

- Added a deterministic regression using the recorded target profile: `gptoss` architecture, 131,072-token model ceiling, 24 blocks, 8 KV heads, 64-element key/value dimensions, 13,793,441,566-byte model weights, and 22.42 GiB currently available physical RAM.
- Under the selector's documented 4 GiB system reserve and 10%-of-weights runtime overhead, the conservative FP16 KV estimate is 49,152 bytes/token and the selected ceiling is 93,699 tokens when weights are not already resident. This leaves the upstream Ollama 64k recommendation feasible for this recorded snapshot while correctly refusing to assume the full 131,072-token model maximum fits.
- This is a deterministic capacity calculation, not a live model-load or performance claim. Runtime allocation and real Codex tool-call behavior still require the self-hosted diagnostic.
- The target profile test is additive; no prior assertions or safety gates were removed.
