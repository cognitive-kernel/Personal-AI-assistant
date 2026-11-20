# Learning-candidate UI isolation — 2026-11-20

## Defects found

A focused review of the temporary learning-candidate panel found a runtime exception in `refreshLearningCandidates()`: the renderer appended a `failure` node that had never been declared. When at least one candidate was returned, the render loop threw a `ReferenceError`; the panel's outer catch replaced the list with a load-failure message.

A second integrity-path defect made a candidate with non-empty but hash-invalid content unreadable through the detail and readiness views. Blocking review and promotion was correct; blocking read-only audit made retained evidence inaccessible and obscured the reason for the integrity failure.

## Correction

- Define the candidate failure/status node before appending it and render the stored `failure_reason` as text, not HTML.
- Extract the complete candidate-panel behavior into `app/static/learning-candidates.js`.
- Keep the dashboard shell in `app/static/index.html`; it now loads the dedicated module after the shared API/DOM helpers are initialized.
- The module boundary owns candidate listing, detail/readiness/history views, review admission, test-plan creation, and sandbox-test dispatch UI. It depends only on the shared `api()` and `el()` helpers; it does not duplicate transport logic.
- Hash-invalid candidate content is now available through a read-only detail path with `integrity_valid=false`. The readiness API reports `integrity_mismatch` and blocks promotion. Claim extraction, review, test-plan writes, and promotion remain fail-closed.
- The detail action remains available for integrity-invalid candidates so retained evidence can be audited; mutation controls remain disabled.
- Added `tests/test_learning_candidate_integrity_readonly.py` for read-only detail, explicit integrity status, blocked claim extraction, and blocked promotion.
- Existing browser regression `test_browser_e2e.py` exercises a terminal contradictory candidate, verifies the rejection/promotion-blocked state is rendered, checks mutation controls remain hidden, and opens retained detail and readiness evidence.

## Validation boundary

- UI fix commit: `5b7bef88107f96b1286dab5bb90b498c4d030353`.
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
