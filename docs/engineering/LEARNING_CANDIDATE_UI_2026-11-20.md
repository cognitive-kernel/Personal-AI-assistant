# Learning-candidate UI isolation — 2026-11-20

## Defect found

A focused review of the temporary learning-candidate panel found a runtime exception in `refreshLearningCandidates()`: the renderer appended a `failure` node that had never been declared. When at least one candidate was returned, the render loop threw a `ReferenceError`; the panel's outer catch replaced the list with a load-failure message. This broke the feature precisely when it had data to display.

## Correction

- Define the candidate failure/status node before appending it and render the stored `failure_reason` as text, not HTML.
- Extract the complete candidate-panel behavior into `app/static/learning-candidates.js`.
- Keep the dashboard shell in `app/static/index.html`; it now loads the dedicated module after the shared API/DOM helpers are initialized.
- The module boundary owns candidate listing, detail/readiness/history views, review admission, test-plan creation, and sandbox-test dispatch UI. It depends only on the shared `api()` and `el()` helpers; it does not duplicate transport logic.
- Existing browser regression `test_browser_e2e.py` exercises a terminal contradictory candidate, verifies the rejection/promotion-blocked state is rendered, checks mutation controls remain hidden, and opens retained detail and readiness evidence.

## Validation boundary

- UI fix commit: `5b7bef88107f96b1286dab5bb90b498c4d030353`.
- Module extraction commits: `824fce6cd899a2adbc4ea543f5ae4f5691631616` and `f492bbeb9faf170a7cb9398ace6460fdae644b79`.
- On exact commit `f492bbeb9faf170a7cb9398ace6460fdae644b79`, Dependency Audit passed and CodeQL passed. Browser E2E and the full tests/Windows-storage workflow were still running at the time of this record.
- No human keyboard/screen-reader review, target-host UI acceptance, or live backend/provider interaction is claimed.

## Acceptance

1. Candidate cards render with populated and empty failure reasons without uncaught browser exceptions.
2. The terminal-candidate panel displays retained content and contradiction/readiness evidence.
3. Terminal candidates cannot invoke review, test-plan, or sandbox mutation controls.
4. Candidate UI logic remains isolated from the dashboard shell and uses the shared API transport.
5. Browser E2E and full exact-head CI must pass before merge.
