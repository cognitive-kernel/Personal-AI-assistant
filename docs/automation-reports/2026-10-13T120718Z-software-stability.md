# Automation run report

- Workstream: Software engineering and stability
- UTC: 2026-10-13T12:07:18Z
- Status: BLOCKED / PARTIAL
- Repo: https://github.com/cognitive-kernel/Personal-AI-assistant

## Starting state
- Main SHA: `f557a807f256bb2023b90174cf673ee5e27d71fd`.
- Inspected PRs #25, #30, #45 and #48. #25 owns a separate catalog test; #30 owns `app/curricula_expansion.py`. No file overlap with `app/curricula.py`.

## Goal and work
- Goal: prevent callers mutating reference metadata returned by curriculum helpers.
- Added `tests/test_curriculum_reference_metadata_isolation.py`; both tests reproduce shared state in `get_curriculum()` and `list_curricula()`.
- Production fix in `app/curricula.py`: NOT DONE; the available write action did not complete.
- Acceptance: regression reproduction PASS; production fix, green tests/CI, review NOT MET.

## Git evidence
- Branch: `fix/curriculum-reference-defensive-copy-20261013T120313Z`
- Start SHA: `f557a807f256bb2023b90174cf673ee5e27d71fd`
- Last code/test SHA: `d01bfbebc05a37165269ff68de9850d1d75c901e`
- Changed file: `tests/test_curriculum_reference_metadata_isolation.py`
- PR: https://github.com/cognitive-kernel/Personal-AI-assistant/pull/48
- Report path: `docs/automation-reports/2026-10-13T120718Z-software-stability.md`

## Tests
- Local pytest: NOT RUN; no local checkout/runtime.
- Exact-head CI: https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/38394846674 on SHA `d01bfbebc05a37165269ff68de9850d1d75c901e`.
- Dependency install passed; pytest: 2 failed, 26 passed (both new regressions).
- Ollama, answer-quality, Recall@5/MRR, curriculum E2E, cross-language retrieval, web fallback: NOT RUN.

## Blockers and next step
- Production file unchanged; PR #48 stays draft and must not merge while tests are red.
- No main/dashboard edit or merge. Coordinator: apply/review defensive copying, rerun pytest, verify final-head CI.
- Decision: NEEDS FOLLOW-UP; reproduction confirmed, fix incomplete.
