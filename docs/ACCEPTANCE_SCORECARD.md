# Weighted engineering and acceptance scorecard

**Snapshot:** 2026-10-31  
**Repository:** `cognitive-kernel/Personal-AI-assistant`  
**Scored baseline:** `main` at `6f601e8cc210419392bfbd1affb9590db978dd3a`  
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
| AI/LLM implementation and learning quality controls | 12 | AUTOMATED TESTED | 0.65 | 7.80 | Learning, retrieval, provenance, language and queue controls have automated coverage; live semantic quality and adjudication remain open. |
| Persistent learning and feedback governance | 8 | CI VERIFIED | 0.75 | 6.00 | Published-only retrieval across a fresh process, feedback boundaries, and durable automatic follow-up have exact-head CI evidence; live quality remains open. |
| Security implementation | 10 | CI VERIFIED | 0.75 | 7.50 | Loopback, URL, redirect/proxy, source egress, Origin/Fetch Metadata and anti-framing controls have CI evidence; independent review is a separate external gate. |
| Frontend and functional UI parity | 10 | AUTOMATED TESTED | 0.65 | 6.50 | Chromium E2E covers major workflows and navigation; manual screen-reader, keyboard-only and rendered-state contrast reviews remain open. |
| Test infrastructure and supply chain | 8 | CI VERIFIED | 0.75 | 6.00 | Exact-head and post-merge test, Windows storage, browser E2E, dependency audit and deterministic benchmark workflows passed on PR #664/#667; current main post-merge CI passed. |
| Performance and reliability engineering | 8 | AUTOMATED TESTED | 0.65 | 5.20 | Durable queues, leases, retry/recovery regressions and deterministic benchmarks exist; target-host soak/load/crash evidence is not available. |
| Documentation, developer and operations artifacts | 7 | IMPLEMENTED | 0.50 | 3.50 | Architecture, operations, acceptance and continuity documentation exist; continuity-log PR #668 is open and operator runbooks still need target-host rehearsal. |
| Target-host startup, recovery, load/soak and rollback | 2 | BLOCKED — external | 0.00 | 0.00 | Requires the intended Windows host and operator-generated redacted evidence. |
| Live Persian/English model evaluation and human adjudication | 1 | BLOCKED — human | 0.00 | 0.00 | Requires a pinned live model run and independent human scoring of the versioned evaluation set. |
| Independent security review | 2 | BLOCKED — external | 0.00 | 0.00 | Issue #52 requires an independent reviewer; implementer CI cannot satisfy this gate. |
| **Total** | **100** |  |  | **54.50** |  |

## Calculations

- **Engineering completion:** repository-engineering points divided by repository-engineering weight. External-only gates (target-host, human adjudication, independent security review) are excluded from this denominator: `54.50 / 95 × 100 = 57.37%`.
- **Overall acceptance progress:** all weighted points divided by 100, including external gates: `54.50 / 100 × 100 = 54.50%`.

These are **provisional, evidence-based progress scores** using the fixed stage factors above. The weights express project risk/prioritization, not statistical confidence. The score must be recalculated when a gate changes state, new evidence is accepted, or the scope/weights are revised. Do not interpret the score as a claim that 57.37% of source lines or features are complete.

## Current exact-SHA evidence

- PR #664 exact head `19a95d93702d6281bdf6ae065a309a9b133de746`: 1,800 passed / 47 deselected; Windows storage, Chromium dashboard smoke, dependency audit, 88 Markdown files, import-cycle checks and benchmark 10/10 passed. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570053) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570102) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212570079).
- PR #667 exact head `810fe450668ff6c3e0e5b74c286533abb32dfb55`: 1,801 passed / 47 deselected; Windows storage, Chromium browser E2E, dependency audit, 87 Markdown files and benchmark 10/10 passed. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765586) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765242) · [Audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211765399).
- On scored `main` `6f601e8cc210419392bfbd1affb9590db978dd3a`, Post-merge CI on `6f601e8cc210419392bfbd1affb9590db978dd3a` passed: tests [#41212956247](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956247) — **1801 passed, 47 deselected**, Windows storage regressions, Markdown validation (88 files), import-cycle checks and deterministic benchmark (10/10); Chromium browser E2E [#41212956268](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956268) and dependency audit [#41212956295](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41212956295) also passed.

## Rules against score inflation

1. Never count deselected tests as passed. Report passed, failed, skipped and deselected separately.
2. Never transfer exact-PR-head evidence to a different SHA without post-merge verification.
3. Never count human, target-host or independent-review gates as repository engineering completion.
4. A deterministic or lexical benchmark is not proof of factual correctness or live-model quality.
5. Keep the score below 100 while any required acceptance gate is blocked or unverified.
6. Record changes to weights, status factors or scope with rationale and a dated engineering-log entry.
