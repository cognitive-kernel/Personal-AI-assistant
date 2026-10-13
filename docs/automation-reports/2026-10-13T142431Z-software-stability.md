# Automation run report

- **Workstream:** Software engineering and stability
- **Report timestamp (UTC):** 2026-10-13T14:24:31Z
- **Automation platform run ID:** Not exposed by automation platform
- **Status:** PARTIAL
- **Repository:** https://github.com/cognitive-kernel/Personal-AI-assistant

## Starting state
- Starting PR #48 head: `47590022e852c4e5c09d6b2c5fbbaedf158ca2d2`; main SHA: `f557a807f256bb2023b90174cf673ee5e27d71fd`.
- Inspected open PR inventory and changed-file ownership. PR #48 owns `app/curricula.py` and `tests/test_curriculum_reference_metadata_isolation.py`; PR #30 owns `app/curricula_expansion.py`; PR #25 covers catalog contract tests; PRs #21/#45 cover answer-quality data validation.
- Prior exact-head CI for PR #48 on the starting head passed: [run 38402458202](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38402458202), 28 tests passed. That result predates this run's additional test assertion and is not claimed as validation of the new change.

## Goal and acceptance criteria
- Intended outcome: strengthen regression coverage so both curriculum getters are proven not to expose recursively nested reference metadata.
- Acceptance criteria:
  - [x] Add nested metadata to the fixture and mutate it through both public getter results.
  - [x] Keep the test change isolated to the existing PR #48 branch; no production code or unrelated workstream changes.
  - [ ] Verify the new test/full suite in CI on the report-inclusive head.
  - [ ] Obtain independent review; no merge performed.

## Actual work performed
- Added a nested `metadata.license.name` field to the reference fixture.
- Extended both getter regression tests to mutate that nested field and assert subsequent getter results remain equal to the original catalog data.
- Files changed in this run: `tests/test_curriculum_reference_metadata_isolation.py` and this report file.
- Deliberately not performed: changes to `docs/PROJECT_PROGRESS.md`, curriculum expansion, PR merge, force-push, or local test execution.

## Git evidence
- Working branch: `fix/curriculum-reference-defensive-copy-20261013T120313Z`.
- Starting SHA: `47590022e852c4e5c09d6b2c5fbbaedf158ca2d2`.
- Code/test change commit: [d22e6f976c2474a98d007c02eadad2c9aad01e17](https://github.com/cognitive-kernel/Personal-AI-assistant/commit/d22e6f976c2474a98d007c02eadad2c9aad01e17).
- Pull request: https://github.com/cognitive-kernel/Personal-AI-assistant/pull/48
- Report path: `docs/automation-reports/2026-10-13T142431Z-software-stability.md`.
- The code/test end SHA before the report artifact commit is `d22e6f976c2474a98d007c02eadad2c9aad01e17`; the report artifact commit and report-inclusive head will be verified from GitHub after this file is committed.

## Tests and validation
- Local `pytest tests/test_curriculum_reference_metadata_isolation.py`: NOT RUN; no local repository checkout/test runtime is available through this connector session.
- Local `pytest`: NOT RUN for the same reason.
- New test commit exact-head CI lookup: no workflow runs or combined statuses returned at the time this report was prepared; validation is pending and no pass is claimed for the new assertions.
- Prior baseline only: [run 38402458202](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38402458202), `pytest` passed with 28 tests on starting SHA `47590022e852c4e5c09d6b2c5fbbaedf158ca2d2`.
- Recall@5/MRR, model-scored Persian/English answer-quality evaluation, curriculum discovery/ingestion E2E, cross-language retrieval, web fallback, provenance/conflict checks, and live Ollama: NOT RUN in this execution.

## Security, regression, and overlap review
- Risk addressed: a shallow copy at either the outer-list or nested-dictionary level could allow a caller to mutate catalog-owned metadata.
- Related PRs compared: #25, #30, #45, #48. The added assertions are limited to PR #48's already-owned test file; no cross-PR files were edited.
- No production behavior changed in this run. The existing deep-copy fix remains under review.

## Blockers and remaining work
- Blockers: exact-head CI for the new test and report-inclusive head is pending; no independent review is recorded.
- Acceptance criteria not met: new-test CI, final-head CI verification, independent review.
- Next smallest actionable step: verify CI on the report-inclusive head, update PR #48's body with this report and exact-head CI evidence, and leave the PR unmerged for review.

## Coordinator verification (coordinator fills this section; workers leave it pending)
- Report and evidence links checked: pending.
- Exact-head CI verified: pending for report-inclusive head.
- Attribution verified: pending.
- Decision: NEEDS FOLLOW-UP.
- Reason: nested-metadata regression assertions were added, but CI on the new and report-inclusive heads and independent review remain outstanding.
