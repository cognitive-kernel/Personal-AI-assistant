# Learning-candidate UI isolation — 2026-11-20

## Defects found

A focused review of the temporary learning-candidate panel found a runtime exception in `refreshLearningCandidates()`: the renderer appended a `failure` node that had never been declared. When at least one candidate was returned, the render loop threw a `ReferenceError`; the panel's outer catch replaced the list with a load-failure message.

A second integrity-path defect made a candidate with non-empty but hash-invalid content unreadable through the detail and readiness views. Blocking review and promotion was correct; blocking read-only audit made retained evidence inaccessible and obscured the reason for the integrity failure.

The browser regression also exposed a task-dashboard runtime error: a candidate-card failure node had been inserted into the task-card renderer, where it referenced an undefined `item` variable. This caused `refreshTasks()` to catch `ReferenceError: item is not defined`, so task cards were never rendered. The misplaced node was removed from task rendering; the candidate-specific failure node remains in its own module.

## Correction

- Define the candidate failure/status node before appending it and render the stored `failure_reason` as text, not HTML.
- Extract the complete candidate-panel behavior into `app/static/learning-candidates.js`.
- Keep the dashboard shell in `app/static/index.html`; it now loads the dedicated module after the shared API/DOM helpers are initialized.
- The module boundary owns candidate listing, detail/readiness/history views, review admission, test-plan creation, and sandbox-test dispatch UI. The dashboard shell exposes an explicit frozen `window.PersonalAssistantUI` contract containing only `api` and `el`; the feature module receives these dependencies through that contract and does not duplicate transport logic or depend on incidental globals.
- Hash-invalid candidate content is now available through a read-only detail path with `integrity_valid=false`. The readiness API reports `integrity_mismatch` and blocks promotion. Claim extraction, review, test-plan writes, and promotion remain fail-closed.
- The detail action remains available for integrity-invalid candidates so retained evidence can be audited; mutation controls remain disabled.
- Added `tests/test_learning_candidate_integrity_readonly.py` for read-only detail, explicit integrity status, blocked claim extraction, and blocked promotion. The exact-hash recovery regression seeds the durable `learning_jobs.lesson` source explicitly before verifying restoration.
- Browser regressions in `test_browser_e2e.py` exercise terminal contradictory candidates and hash-invalid read-only audit, and now verify task cards render after refresh without uncaught browser exceptions. They verify rejection/promotion-blocked state, hidden mutation controls, retained detail/readiness evidence, and the task cancellation workflow.

## Validation boundary

- UI fix commits: `5b7bef88107f96b1286dab5bb90b498c4d030353`, `05161175f39051e10034647bab42cfebfa3c7cc2`, and `eefb42988d5b777bb7898baa445b5d4d9072af0e`.
- Module extraction commits: `824fce6cd899a2adbc4ea543f5ae4f5691631616` and `f492bbeb9faf170a7cb9398ace6460fdae644b79`.
- Read-only integrity commits: `5282b90e6f3ff43bcf4571dc991baecbf178cb0d`, `2fcbad659558ac295a9329f1e3cb8a43910059cf`, `f789e6db2a37b14119d3b446f1e7b5dbd0c1c820`, and `68fb3df53e0cdc1a765c9abaf57cac95913b0831`.
- On exact commit `f492bbeb9faf170a7cb9398ace6460fdae644b79`, Dependency Audit and CodeQL passed; Browser E2E and full tests/Windows storage were still running. The later integrity changes require fresh exact-head CI.
- No human keyboard/screen-reader review, target-host UI acceptance, or live backend/provider interaction is claimed.

## Acceptance

1. Candidate cards render with populated and empty failure reasons without uncaught browser exceptions.
2. Terminal candidates display retained content and contradiction/readiness evidence.
3. Hash-invalid content is readable only for audit and cannot enter claim extraction, review, testing, or promotion.
4. Terminal candidates cannot invoke review, test-plan, or sandbox mutation controls.
5. Candidate UI logic remains isolated from the dashboard shell and uses the shared API transport.
6. Browser E2E and full exact-head CI must pass before merge.
