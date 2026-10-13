# Automation run report

- **Workstream:** Software engineering and stability
- **Report timestamp (UTC):** 2026-10-13T13:06:20Z
- **Automation platform run ID:** Not exposed by automation platform
- **Status:** PARTIAL
- **Repository:** https://github.com/cognitive-kernel/Personal-AI-assistant

## Starting state
- Branch starting SHA: `79c4f3d148c83753089c75aec9e1998a4eda734e`; main SHA: `f557a807f256bb2023b90174cf673ee5e27d71fd`.
- Inspected open PRs #25 (catalog contract tests), #30 (curriculum expansion), #45 (answer-quality JSON validation), and #48 (this defect). No other open PR owns `app/curricula.py`; #30 owns `app/curricula_expansion.py`.
- Recent failed exact-head CI on the starting branch: [run 38395527909](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38395527909), SHA `79c4f3d148c83753089c75aec9e1998a4eda734e`; `pytest` reported 2 failed, 26 passed. The failures reproduced mutation of shared reference metadata.

## Goal and acceptance criteria
- Intended outcome: prevent callers of `get_curriculum()` and `list_curricula()` from mutating catalog-owned nested `reference_urls` metadata.
- Acceptance criteria:
  - [x] Reproduce both getter paths before the fix; exact-head CI failed on both regression tests.
  - [x] Deep-copy reference metadata in both helpers.
  - [x] Full CI test command passes on the application-fix commit: 28 passed.
  - [ ] Verify CI on the report-inclusive PR head.
  - [ ] Obtain independent review; no merge performed.

## Actual work performed
- Added `from copy import deepcopy` and deep-copied `reference_urls` in both public curriculum read helpers.
- Retained the two regression tests added by the preceding run; they cover mutation of nested dictionaries and list entries.
- Files changed in this run: `app/curricula.py`; this report file. The existing regression test file was not modified in this run.
- Deliberately not performed: edits to `docs/PROJECT_PROGRESS.md`, unrelated curriculum-expansion changes, PR merge, force-push, or local test execution.

## Git evidence
- Working branch: `fix/curriculum-reference-defensive-copy-20261013T120313Z`.
- Starting SHA: `79c4f3d148c83753089c75aec9e1998a4eda734e`.
- Application-fix commit / tested head: [`faa18549bd6d3a55a5d10139003d4beca3703cc9`](https://github.com/cognitive-kernel/Personal-AI-assistant/commit/faa18549bd6d3a55a5d10139003d4beca3703cc9).
- Pull request: https://github.com/cognitive-kernel/Personal-AI-assistant/pull/48
- Report path: `docs/automation-reports/2026-10-13T130620Z-software-stability.md`.
- The report-file commit follows the tested application commit. The final report-inclusive PR head and its CI result will be verified and recorded in the PR body after this file is committed.

## Tests and validation
- Local `pytest tests/test_curriculum_reference_metadata_isolation.py`: NOT RUN; this connector session has no local repository checkout/test runtime.
- CI command: `python -m pip install -e ".[dev]"` — PASS.
- CI command: `pytest` — PASS, **28 passed in 0.36s**, on exact application-fix SHA `faa18549bd6d3a55a5d10139003d4beca3703cc9`.
- Exact-head CI: [run 38402290412](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38402290412).
- Prior failing reproduction: [run 38395527909](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38395527909), 2 failed / 26 passed on starting SHA.
- Recall@5/MRR, model-scored Persian/English answer-quality evaluation, curriculum discovery/ingestion E2E, cross-language retrieval, web fallback, provenance/conflict checks, and live Ollama: NOT RUN in this execution.

## Security, regression, and overlap review
- Root cause: `list(item.get("reference_urls", []))` copied only the outer list; `get_curriculum()` used a shallow `item.copy()`. Nested dictionaries and, in the latter path, the list itself remained shared with `CURRICULA`.
- Fix: `deepcopy(item.get("reference_urls", []))` in both helpers; no prompt, source-ingestion, or security-policy behavior changed.
- Related PRs compared: #25, #30, #45, #48. No file overlap with the production file was found in those PRs.
- Remaining risk: independent review and report-inclusive exact-head CI are still pending.

## Product-contract acceptance snapshot
- Curriculum catalog defensive-copy contract: **IMPLEMENTED on tested commit** — `app/curricula.py`, regression coverage in `tests/test_curriculum_reference_metadata_isolation.py`, PR #48; report-inclusive CI/review pending.
- Dynamic curriculum expansion and official-source ingestion: **PARTIAL** — `app/curricula_expansion.py`, PR #30; discovery/ingestion end-to-end validation was NOT RUN here.
- Persian/English answer-quality dataset: **PARTIAL** — PRs #21/#45; model-scored quality evaluation was NOT RUN here.
- Cross-language retrieval and Recall@5/MRR: **PARTIAL / NOT VERIFIED** — PR #8; prepared indexed-corpus metrics were NOT RUN here.
- Source provenance, bounded web fallback, conflict handling, and evidence-status experience memory: **PARTIAL / NOT VERIFIED** — related work includes PRs #6/#8/#11; end-to-end checks were NOT RUN here.
- Live Ollama smoke/integration: **NOT RUN** — no live-model result was available in this connector execution.

## Blockers and remaining work
- Blockers: exact-head CI on the report-inclusive PR head and independent review remain pending; local tests were unavailable.
- Acceptance criteria not met: report-inclusive exact-head CI, independent review, and broader product-contract validations.
- Next smallest actionable step: verify CI on the report-inclusive head, update PR #48's body with the report link and exact-head CI evidence, and leave the PR unmerged for review.

## Coordinator verification (coordinator fills this section; workers leave it pending)
- Report and evidence links checked: pending.
- Exact-head CI verified: pending for report-inclusive head.
- Attribution verified: pending.
- Decision: NEEDS FOLLOW-UP.
- Reason: the reproduced regression is fixed and the full suite passed on the application-fix commit; final-head CI and independent review are still required.
