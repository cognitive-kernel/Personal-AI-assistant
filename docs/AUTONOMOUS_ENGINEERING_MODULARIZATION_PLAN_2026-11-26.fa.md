# Autonomous Engineering — Backend Modularization Plan (2026-11-26)

## Current integration snapshot

- Integration PR: [#872](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/872), Draft; target `main`.
- Integration branch: `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`.
- Snapshot before this documentation update: `e7924332aed26d05f75b6e63b2d6acff91450dfb`.
- PR #877 (backend extraction) and #878 (test integrity regression) have been merged, in order, into the integration branch.
- PR #879 is a child PR targeting the integration branch. Its current head is `66cefdf44f7a56fb8aade5cd75e5a7c0d566b7ef`; it remains Draft and is not yet merged.
- Superseded documentation PRs #880 and #881 are closed. Their useful dependency-isolation requirements are preserved in the current #879 branch; current canonical documents on `main` were not overwritten.
- No user-host task, Runner, Broker service, ACL, secret, repository variable, user database, model, or checkpoint was changed during this repository audit.

## Evidence-based progress

1. The integration branch originally contained `app/autonomous_engineering.py` at 1,626 lines.
2. PR #877 extracted request contracts, structured API errors, GitHub adapters, PR preflight, progress projection, readiness, run history/log safety/log retrieval, and settings into dedicated modules. The facade is now 1,153 lines. Existing route names and compatibility seams were retained.
3. PR #878 found and fixed five duplicate top-level pytest function names in the State Broker suite. Python had silently shadowed the earlier function definitions during import. The original test bodies and assertions were retained, and a regression guard now rejects duplicate test names.
4. The current `tests/test_autonomous_engineering_module_boundaries.py` validates UI module load order, versioned browser contracts, selector consistency, fault isolation, and several backend single-owner delegation seams. It is not yet a complete Python architecture fitness suite.
5. The backend facade still contains dispatch and run orchestration, state coordination, and compatibility exports. The modularization gate remains open.

## Target module boundaries

| Module | Owns | Must not own |
|---|---|---|
| `autonomous_engineering_contracts.py` | request models and stable protocol constants | provider calls, persistence |
| `autonomous_engineering_errors.py` | structured API error envelopes and route error policy | workflow state |
| `autonomous_engineering_readiness.py` | readiness projection and pure status assembly | dispatch mutation, settings writes |
| `autonomous_engineering_settings.py` | settings validation/persistence and repository-variable adapter | workflow execution |
| `autonomous_engineering_github.py` | narrow GitHub CLI/API adapter | business policy and UI |
| `autonomous_engineering_run_history.py` | verified run history and diagnostics cache | dispatch ownership |
| `autonomous_engineering_run_logs.py` / `autonomous_engineering_run_log_safety.py` | log retrieval and sanitization | task/lease policy |
| `autonomous_engineering_pr_preflight.py` | open-PR preflight policy | GitHub transport implementation |
| `autonomous_engineering_progress_projection.py` | pure workflow-job to progress projection | subprocess or network access |
| `autonomous_engineering_dispatch.py` | dispatch admission, dispatch, Pause/Resume/Cancel orchestration | direct UI state, duplicate lock implementations |
| `autonomous_engineering_runs.py` | run listing, verified logs/diagnostics, cleanup orchestration | task ownership and lease policy |
| `autonomous_engineering.py` | FastAPI router composition and temporary backward-compatible exports | domain logic, mutable workflow state |

The existing progress, State Broker/native state, checkpoint, dispatch-lock, execution-lock, model-pull, and Ollama-context modules remain separate owners. No parallel state store or duplicate lock implementation is permitted.

## Dependency direction and required architecture fitness tests

```text
FastAPI facade/router
  -> feature route adapters
      -> application services
          -> domain policies/contracts
          -> persistence and provider ports
              -> State Broker / native state / GitHub / Ollama adapters
```

Required enforceable rules:

- The facade may compose feature routers; feature modules must never import the facade.
- No circular imports or hidden import-order dependency.
- No private cross-module imports except explicitly documented compatibility shims.
- Shared contracts remain dependency-light and perform no import-time external operations.
- Mutable caches, executors, locks, and task state have exactly one owner.
- Enforce dependency direction and ownership by executable tests, not only source-string assertions.
- Preserve all public HTTP contracts and current test monkeypatch seams until callers migrate deliberately.

## Native state runtime — PR #879

ADR-025 defines the target design: one application-owned Python state adapter, OS-managed cross-process locks, same dynamically discovered interactive identity for UI and Runner, non-destructive legacy migration, and exact process/run ownership for stale-run recovery.

Implementation is incomplete:
- Windows C# Broker Service, named-pipe client, PowerShell provisioning, and machine-wide state configuration remain in the current runtime path.
- POSIX broker daemon/configuration also remains.
- Native Python state ownership has not yet replaced those paths.
- Migration must preserve current progress, events, history, pending task/PR records, lock files, and checkpoint bytes; ambiguous ownership must fail closed.
- Do not uninstall or mutate an existing user Broker/Runner/service/ACL as part of CI or automatic migration.

PR #879 previously exposed a Windows storage integration test failure. The failure was traced to a nondeterministic cleanup test fixture: an earlier successful cleanup had already removed the temporary file that a later assertion expected. The new head adds a fresh explicit `scorecard-state.json.tmp` fixture before the cleanup assertion. Exact-head CI for the new head must complete before that test is considered fixed.

## Required implementation sequence

1. Complete PR #879's exact-head tests and inspect all Windows/Linux job results. Do not treat the previous failed SHA as evidence for the corrected head.
2. Complete the service-free native state adapter and same-user Runner lifecycle, or split the work into a narrowly scoped child PR with explicit dependency ordering. Do not merge a proposal-only ADR as an implementation.
3. Add non-destructive migration and cross-platform tests for clean startup, existing legacy state, active/stale/unknown owners, PID reuse, watcher locks, crash/restart, and checkpoint preservation.
4. Extract dispatch and run orchestration into dedicated modules one boundary at a time; retain compatibility shims only while needed.
5. Add Python architecture fitness tests for import cycles, dependency direction, facade imports, private cross-module access, and single-owner mutable state.
6. Run targeted tests after each extraction, then complete Linux/Windows suite, Browser E2E, CodeQL, and dependency audit on the final exact SHA.
7. Keep #872 Draft until the owner runs Windows target-host acceptance: non-admin startup, no mandatory PowerShell/Windows Service dependency, real Runner/Ollama/Codex dispatch, progress/PR evidence, Pause/Resume/Cancel, and crash/restart checkpoint recovery.

## Merge gate

A green CI run does not waive target-host acceptance. Do not merge #879 or #872 into `main` until:

- all required workflows are green on each final exact head;
- native state runtime and migration are implemented and verified;
- Python and UI architecture fitness tests pass;
- Windows target-host acceptance evidence is recorded;
- no active task/checkpoint/Runner state was altered during validation;
- PR description, project status, engineering log, ADR, and this plan agree on the final SHA and remaining risks.
