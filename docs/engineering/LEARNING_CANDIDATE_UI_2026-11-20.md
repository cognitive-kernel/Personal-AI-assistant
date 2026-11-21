# Learning-candidate UI isolation — 2026-11-20

## Defects found

The candidate panel's original failure/status node was already correctly scoped. The actual runtime defect exposed by Browser E2E was in the task dashboard: an earlier UI edit had placed a candidate-specific failure node inside the task-card renderer, where it referenced undefined `item`. `refreshTasks()` caught `ReferenceError: item is not defined`, so task cards were never rendered. The misplaced node was removed from task rendering; the candidate-specific node remains in its own module.

A separate integrity-path defect made a candidate with non-empty but hash-invalid content unreadable through the detail and readiness views. Blocking review and promotion was correct; blocking read-only audit made retained evidence inaccessible and obscured the reason for the integrity failure.

## Correction

- Keep the candidate failure/status node scoped to the candidate card and render stored `failure_reason` as text, not HTML; remove the misplaced candidate node from task-card rendering.
- Extract the complete candidate-panel behavior into `app/static/learning-candidates.js`.
- Keep the dashboard shell in `app/static/index.html`; it now loads the dedicated module after the shared API/DOM helpers are initialized.
- The module boundary owns candidate listing, detail/readiness/history views, review admission, test-plan creation, and sandbox-test dispatch UI. The dashboard shell and feature modules now share the versioned `window.dashboardCore` v1 contract (`api`, `el`, and `appendText`) from `app/static/dashboard-core.js`. The candidate module validates the contract version before binding its UI and does not duplicate transport logic or depend on incidental globals. The core file is byte-identical to the shared core introduced in PR #795.
- Hash-invalid candidate content is now available through a read-only detail path with `integrity_valid=false`. The readiness API reports `integrity_mismatch` and blocks promotion. Claim extraction, review, test-plan writes, and promotion remain fail-closed.
- The detail action remains available for integrity-invalid candidates so retained evidence can be audited; mutation controls remain disabled.
- Added `tests/test_learning_candidate_integrity_readonly.py` for read-only detail, explicit integrity status, blocked claim extraction, and blocked promotion. The exact-hash recovery regression seeds the durable `learning_jobs.lesson` source explicitly before verifying restoration.
- Browser regressions in `test_browser_e2e.py` exercise terminal contradictory candidates and hash-invalid read-only audit, and now verify task cards render after refresh without uncaught browser exceptions. They verify rejection/promotion-blocked state, hidden mutation controls, retained detail/readiness evidence, and the task cancellation workflow.

## Validation boundary

- UI/module commits include the task-card renderer correction, the shared `dashboardCore` v1 contract, and candidate-module integration; latest validated code/test head: `b3c1c9df3ba73ac854461754631e77cc03d74dc2`.
- Module extraction commits: `824fce6cd899a2adbc4ea543f5ae4f5691631616` and `f492bbeb9faf170a7cb9398ace6460fdae644b79`.
- Read-only integrity commits: `5282b90e6f3ff43bcf4571dc991baecbf178cb0d`, `2fcbad659558ac295a9329f1e3cb8a43910059cf`, `f789e6db2a37b14119d3b446f1e7b5dbd0c1c820`, and `68fb3df53e0cdc1a765c9abaf57cac95913b0831`.
- Exact-head validation passed on `b3c1c9df3ba73ac854461754631e77cc03d74dc2`: 2,441 Python tests passed, 14 skipped, 65 deselected; Browser E2E, Windows storage/LocalService broker, Dependency Audit, CodeQL, generated-artifact validation, Markdown links, import-cycle checks, and retrieval/claim-coverage benchmarks passed.
- No human keyboard/screen-reader review, target-host UI acceptance, or live backend/provider interaction is claimed.

## Acceptance

1. Candidate cards render with populated and empty failure reasons without uncaught browser exceptions.
2. Terminal candidates display retained content and contradiction/readiness evidence.
3. Hash-invalid content is readable only for audit and cannot enter claim extraction, review, testing, or promotion.
4. Terminal candidates cannot invoke review, test-plan, or sandbox mutation controls.
5. Candidate UI logic remains isolated from the dashboard shell and uses the shared API transport.
6. Browser E2E and full exact-head CI must pass before merge.
