# Autonomous Engineering — Backend Modularization Plan (2026-11-26)

## Decision record

PR #872 remains the single integration PR and stays Draft. It is not cancelled because its current exact-head CI is green and it contains the consolidated runtime fixes. It is not merge-ready because the backend decomposition gate and Windows target-host acceptance are still open.

A remediation branch is based on the exact current PR head:
- Base SHA: `18bcba48e843a5920e27c33f51f4bf98ec1728aa`
- Branch: `fix/autonomous-engineering-modular-boundaries-20261126`

No runtime task, Runner, Broker, Windows service, ACL, secret, repository variable, user database, model, or checkpoint was changed during this audit.

## Evidence-based findings

1. `app/autonomous_engineering.py` is 1,626 lines and contains multiple independently changing concerns:
   - HTTP error envelope and route policy
   - readiness/status probes
   - model and Broker setup endpoints
   - progress enrichment
   - settings persistence and GitHub variable updates
   - dispatch admission, Pause/Resume/Cancel
   - GitHub API access, workflow/run diagnostics, logs, and cleanup
2. `tests/test_autonomous_engineering_module_boundaries.py` currently validates browser JavaScript module load order, versioned UI contracts, selector consistency, and UI isolation. It does not enforce Python backend dependency direction, import-cycle absence, or backend module ownership.
3. Existing tests import `app.autonomous_engineering` as a compatibility surface and monkeypatch many symbols on that module. Refactoring must preserve route behavior and update test seams deliberately; moving functions without updating dependency injection would silently invalidate test coverage.
4. Existing Broker lease/fencing, checkpoint, dispatch-admission, and execution-lock implementations are established contracts and must be reused, not reimplemented.

## Target module boundaries

| Module | Owns | Must not own |
|---|---|---|
| `autonomous_engineering_contracts.py` | request/response schemas, stable error codes, correlation/error envelope | provider calls, persistence |
| `autonomous_engineering_readiness.py` | component readiness probes, bounded probe scheduling, readiness projection | dispatch mutation, settings writes |
| `autonomous_engineering_settings.py` | settings validation/persistence and GitHub variable adapter | workflow execution |
| `autonomous_engineering_dispatch.py` | admission, dispatch, Pause/Resume/Cancel orchestration | direct UI state, duplicate lock implementations |
| `autonomous_engineering_runs.py` | run listing, verified logs/diagnostics, cleanup orchestration | task ownership and lease policy |
| `autonomous_engineering_github.py` | narrow GitHub CLI/API adapter, sanitization and response validation | business policy and UI |
| `autonomous_engineering.py` | FastAPI router composition and temporary backward-compatible exports | domain logic, mutable workflow state |

The existing `autonomous_engineering_progress.py`, State Broker, checkpoint, dispatch-lock, execution-lock, model-pull, and Ollama-context modules remain separate owners. No new parallel state store or lock is permitted.

## Dependency direction

```text
FastAPI facade/router
  -> feature route adapters
      -> application services
          -> domain policies/contracts
          -> persistence and provider ports
              -> State Broker / checkpoint / GitHub / Ollama adapters
```

- The facade may import feature routers; feature modules must never import the facade.
- Shared contracts must be small, dependency-light, and free of import-time external operations.
- Cross-feature communication uses explicit service interfaces or typed contracts.
- Mutable caches, executors, locks, and task state have exactly one owner.
- No circular imports, private cross-module imports, shell-string interpolation, or hidden import-order dependency.

## Reconciliation of the two master prompts

The continuity prompt requires automatic acquisition of a missing required prerequisite; the UI-first prompt requires read-only startup/preflight, explicit user action, and consent before external changes. These are reconciled as follows:

- Never install, update, start, or repair external tools during ordinary application startup or read-only readiness checks.
- When the user invokes a capability that requires a missing prerequisite, the application may automatically perform the acquisition workflow only under the user's configured policy and required consent.
- Verify source, signature/hash, compatibility, cache, atomic install, rollback, and post-install capability detection. If consent, network, or verification is unavailable, block only the dependent capability and preserve all unrelated features.
- Never silently substitute models/providers or mutate machine-wide state without explicit least-privilege approval.

This preserves the stronger safety boundary while retaining automatic, resumable provisioning within an explicitly invoked capability.

## Required implementation sequence

1. Add characterization tests for current public routes, response envelopes, state transitions, and compatibility exports.
2. Extract the GitHub adapter and stable contracts; prove no behavior change.
3. Extract readiness, settings, dispatch, and run orchestration one boundary at a time, keeping state ownership unchanged.
4. Replace compatibility exports only after all callers/tests use the owning module; retain documented compatibility shims where externally consumed.
5. Add Python architecture fitness tests for import cycles, forbidden facade imports, dependency direction, private cross-module access, and single-owner mutable state.
6. Run targeted tests after each extraction, then the complete Linux/Windows suite, Browser E2E, CodeQL, and dependency audit on the final exact SHA.
7. Keep PR #872 Draft until the owner runs the required Windows target-host acceptance: non-admin startup, explicit Broker setup, real Runner/Ollama/Codex dispatch, progress/PR evidence, Pause/Resume/Cancel, and crash/restart checkpoint recovery.

## Merge gate

A green CI run does not waive target-host acceptance. Do not merge this remediation or PR #872 into `main` until:
- all required workflows are green on the final exact head;
- Python and UI architecture fitness tests pass;
- target-host Windows acceptance evidence is recorded;
- no active task/checkpoint/Runner state was altered during validation;
- PR description, project status, and engineering log agree on the final SHA and remaining risks.
