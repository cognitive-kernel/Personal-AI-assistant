# Weighted engineering and acceptance scorecard

**Snapshot:** 2026-11-01  
**Repository:** `cognitive-kernel/Personal-AI-assistant`  
**Scored baseline:** `main` at `2a5c83bf0ba924f4a2c8f206338a04872562d72d`  
**Purpose:** Reproducible progress accounting required by `docs/MASTER_ENGINEERING_PROMPT.md`. This is a provisional engineering-progress measure, not a release-readiness or product-quality certification.

## Scoring method

Each gate has a fixed weight. The evidence state maps to a fixed progress factor:

| State | Factor | Minimum evidence |
|---|---:|---|
| NOT STARTED / BLOCKED | 0.00 | No accepted repository evidence, or a required external prerequisite is unavailable |
| IN PROGRESS | 0.25 | Scope and work started; acceptance is incomplete |
| IMPLEMENTED | 0.50 | Implementation/artifacts exist; verification is incomplete |
| AUTOMATED TESTED | 0.65 | Relevant automated tests pass on an identified commit |
| CI VERIFIED | 0.75 | Required exact-SHA CI workflows pass |
| ACCEPTED | 1.00 | All gate-specific acceptance evidence is present and reviewed |

A gate cannot be marked ACCEPTED solely because code exists or CI is green. If evidence applies only to a PR head, it is described as exact-head evidence and is not silently promoted to post-merge evidence. Post-merge CI is tracked separately.

## Weighted gate matrix

| Gate | Weight | Current state | Factor | Weighted points | Evidence / remaining condition |
|---|---:|---|---:|---:|---|
| Repository and architecture audit | 12 | IN PROGRESS | 0.25 | 3.00 | Architecture map and module inventory exist; comprehensive risk-ranked audit and file/line coverage are not complete. |
| Backend and API contracts | 10 | IN PROGRESS | 0.25 | 2.50 | Strict validation and API regressions exist; a complete endpoint-by-endpoint contract audit is not evidenced. |
| Database and data integrity | 10 | AUTOMATED TESTED | 0.65 | 6.50 | Migration, restore, staging and Windows storage regression coverage exists; target-host migration evidence remains owner-reported/unverified. |
| AI/LLM implementation and learning quality controls | 12 | CI VERIFIED | 0.75 | 9.00 | PR #669 added expected-language diagnostics and symmetric mixed-script regressions; exact-head tests, Windows storage, browser smoke/E2E and dependency audit passed. Live semantic quality and adjudication remain open. |
| Persistent learning and feedback governance | 8 | CI VERIFIED | 0.75 | 6.00 | Published-only retrieval across a fresh process, feedback boundaries, and durable automatic follow-up have exact-head CI evidence; live quality remains open. |
| Security implementation | 10 | CI VERIFIED | 0.75 | 7.50 | Loopback, URL, redirect/proxy, source egress, Origin/Fetch Metadata, anti-framing, and cloud-discovery diagnostic redaction have exact-head CI evidence; independent review is a separate external gate. |
| Frontend and functional UI parity | 10 | AUTOMATED TESTED | 0.65 | 6.50 | Chromium E2E covers major workflows and navigation; manual screen-reader, keyboard-only and rendered-state contrast reviews remain open. |
| Test infrastructure and supply chain | 8 | CI VERIFIED | 0.75 | 6.00 | Exact-head and post-merge test, Windows storage, browser E2E, dependency audit and deterministic benchmark workflows passed on PR #664/#667; current main post-merge CI passed. |
| Performance and reliability engineering | 8 | AUTOMATED TESTED | 0.65 | 5.20 | Durable queues, leases, retry/recovery regressions and deterministic benchmarks exist; target-host soak/load/crash evidence is not available. |
| Documentation, developer and operations artifacts | 7 | CI VERIFIED | 0.75 | 5.25 | PR #668 merged the durable continuity log, weighted scorecard and synchronized status/acceptance documents; exact-head tests, Windows storage, browser smoke/E2E and dependency audit passed. Operator runbooks still need target-host rehearsal. |
| Target-host startup, recovery, load/soak and rollback | 2 | BLOCKED — external | 0.00 | 0.00 | Requires the intended Windows host and operator-generated redacted evidence. |
| Live Persian/English model evaluation and human adjudication | 1 | BLOCKED — human | 0.00 | 0.00 | Requires a pinned live model run and independent human scoring of the versioned evaluation set. |
| Independent security review | 2 | BLOCKED — external | 0.00 | 0.00 | Issue #52 requires an independent reviewer; implementer CI cannot satisfy this gate. |
| **Total** | **100** |  |  | **57.45** |  |

## Calculations

- **Engineering completion:** repository-engineering points divided by repository-engineering weight. External-only gates (target-host, human adjudication, independent security review) are excluded from this denominator: `57.45 / 95 × 100 = 60.47%`.
- **Overall acceptance progress:** all weighted points divided by 100, including external gates: `57.45 / 100 × 100 = 57.45%`.

These are **provisional, evidence-based progress scores** using the fixed stage factors above. The weights express project risk/prioritization, not statistical confidence. The score must be recalculated when a gate changes state, new evidence is accepted, or the scope/weights are revised. Do not interpret the score as a claim that 60.47% of source lines or features are complete.

## Current exact-SHA evidence

- PR #664 exact head `19a95d93702d6281bdf6ae065a309a9b133de746`: 1,800 passed / 47 deselected; Windows storage, Chromium dashboard smoke, dependency audit, 88 Markdown files, import-cycle checks and benchmark 10/10 passed. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570053) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570102) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570079).
- PR #667 exact head `810fe450668ff6c3e0e5b74c286533abb32dfb55`: 1,801 passed / 47 deselected; Windows storage, Chromium browser E2E, dependency audit, 87 Markdown files and benchmark 10/10 passed. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765586) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765242) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765399).
- PR #668 exact head `0bcd2a93bbf4516f180ac9fc3d8c647380545ade` passed tests, Windows storage regressions, dashboard smoke, browser E2E and dependency audit; merged as `eec03b39e53399bb8ee21a15b9e8cb4930159c25`. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673355) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673417) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41213673381).
- On scored `main` `eec03b39e53399bb8ee21a15b9e8cb4930159c25`, dependency audit [#41215789470](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789470) passed; tests [#41215789440](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789440) and browser E2E [#41215789465](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215789465) were in progress at this snapshot.

## Rules against score inflation

1. Never count deselected tests as passed. Report passed, failed, skipped and deselected separately.
2. Never transfer exact-PR-head evidence to a different SHA without post-merge verification.
3. Never count human, target-host or independent-review gates as repository engineering completion.
4. A deterministic or lexical benchmark is not proof of factual correctness or live-model quality.
5. Keep the score below 100 while any required acceptance gate is blocked or unverified.
6. Record changes to weights, status factors or scope with rationale and a dated engineering-log entry.

- PR #669 exact head `bedf5f54bb55dd2cf64b267b9b5561436820ac61` passed **1,807 tests / 47 deselected**, Windows storage regressions, dashboard smoke, browser E2E, dependency audit, 90 Markdown files, import-cycle checks and benchmark 10/10; merged as `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6`. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830531) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830524) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41215830502).
- Post-merge CI on `main` `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6` passed: tests [#41216212310](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212310), browser E2E [#41216212276](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212276), and dependency audit [#41216212245](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216212245).


- PR #670 documentation-only synchronization exact head `1d00000055573acb1edc30e402b7a8692e16ce69` passed tests [#41216298867](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216298867), browser E2E [#41216298909](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216298909), and dependency audit [#41216298986](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41216298986).

## 2026-11-01 checkpoint — PR #671

- PR [#671](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/671) merged as exact `main` SHA `2a5c83bf0ba924f4a2c8f206338a04872562d72d`. Exact PR-head tests [#41221895391](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895391), browser E2E [#41221895422](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895422), and dependency audit [#41221895550](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41221895550) passed.
- Post-merge workflows are not yet complete at this snapshot: tests [#41239355370](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355370) and browser/dashboard smoke [#41239355374](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355374) are in progress; dependency audit [#41239355466](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355466) passed.
- Progress remains **60.47% engineering / 57.45% overall**; the change does not close an external gate.
