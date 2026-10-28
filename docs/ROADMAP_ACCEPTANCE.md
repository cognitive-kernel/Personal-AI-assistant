# Project acceptance gates

This file is the release-gate ledger for tracking issue [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290). It distinguishes repository implementation and CI evidence from target-host operations, live-model evaluation, human adjudication, and independent review. A merged PR is not by itself proof that an acceptance gate passed.

**Status at 2026-10-28:** not accepted for release. Do not describe the project as 100% complete while any required gate below is blocked or unverified.

## Gate ledger

| Gate | Current status | Evidence / next action |
|---|---|---|
| Reviewed legacy learning.db migration | **Implementation merged; Windows tests user-reported successful; issue-specific migration evidence not attached** | PR [#306](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/306) merged the exact-fingerprint, backup-first conversion and regression tests. The migration refuses unknown fingerprints and verifies backup/data/integrity before replacement. Run the read-only diagnostic and migration/startup checks against a **disposable copy** of the affected Windows database first; preserve the original and verified backup. The project owner reports all Windows tests passed (GitHub issue comment, 2026-10-28). The report/log was not attached to the repository, so treat this as user-reported success, not independently verified evidence. Confirm that the run specifically covered this legacy migration on a disposable copy, row preservation, restart, and /api/health before closing issue [#296](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/296). |
| Unknown SQLite schema safety | **Automated coverage present; Windows test suite user-reported successful** | Run the read-only inspector against the intended checkout, for example: python scripts/inspect_sqlite_schema.py .\data\learning.db --kind learning --output C:\Temp\learning-schema-report.json. Do not delete/recreate the database, manually stamp migration history, or run a migration on the live database without a reviewed plan and verified backup. |
| Unit/regression suite and dependency audit | **Windows suite user-reported successful; exact-head GitHub CI still must be checked for the final release commit** | Require successful current GitHub Actions runs on the final commit, including the full test suite, Windows storage regressions where configured, dependency audit, and import-cycle/artifact checks. Historical green runs are not evidence for a later commit. |
| Browser E2E and accessible responsive UI | **Automated coverage merged; full acceptance pending** | CI covers dashboard navigation, focus, control names, CSS loading, RTL/layout behavior, and narrow widths. Still perform keyboard and screen-reader review, contrast review, error/loading/empty states, destructive-action confirmation, and full workflow parity on the target browser/host. |
| Research provenance and source handling | **Implementation/tests merged; live evidence pending** | Run scripts/run_answer_quality_eval.py with the intended Ollama model available. Confirm the report contains the exact commit, dataset hash, model manifest digest, source snapshots, and no unreported generation failures. Do not commit prompts, answers, or local reports. A URL alone does not prove claim support. |
| Live Persian/English answer quality | **Blocked on live run and human adjudication** | Use docs/HUMAN_ADJUDICATION.md and scripts/create_human_adjudication.py to produce a worksheet from the exact report and versioned dataset. Human reviewers must assess correctness, source support/reliability, uncertainty, language consistency, and prompt-injection resistance. Do not substitute lexical diagnostics or model self-review for adjudication. |
| Persistent learning and user-feedback governance | **Automated behavior must be revalidated with live workflow** | Verify feedback remains untrusted until explicit governed review with independent evidence; verify provenance/version history, later-run retrieval, and rollback. Distinguish persistent memory/RAG from model-weight training. |
| Ollama loopback security boundary | **Implementation hardened; independent review BLOCKED** | See docs/security/OLLAMA_LOOPBACK_HARDENING.md and issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52). An independent reviewer other than the implementer must assess IPv4/IPv6 parsing, name resolution/DNS rebinding, redirect/proxy behavior, TLS hostname validation, size/time limits, malformed responses, and data-exfiltration paths; record findings and retest remediations. Automated tests and implementer review do not satisfy this gate. |
| Target-host startup, recovery, load/soak | **Windows tests user-reported successful; detailed recovery/soak evidence not attached** | Run scripts/run_target_host_baseline.py --run-tests --output C:\Temp\target-host-baseline.json in the intended environment. Separately exercise process restart, task crash/retry/cancel/recovery, resource limits, and sustained load. Preserve redacted evidence outside the repository. |
| Backup, staging restore, and rollback | **Blocked on an operator-run staging rehearsal** | Follow docs/DATABASE_BACKUP.md and docs/operations/FINAL_CLEAN_START_ACCEPTANCE.md. Verify backups and foreign keys, restore into disposable staging stores, and rehearse rollback. Do not run destructive repair or reset against live user data. |
| Clean-start / release-store acceptance | **Blocked on release-stage rehearsal** | Use fresh disposable stores and the documented registered-migration path. Verify no test knowledge, feedback, sources, tasks, lessons, or assessments leak into release stores; run post-reset smoke tests. Do not clear databases during ongoing development. |
| Final exact-head CI and release decision | **Not passed** | Before release, verify all required workflows on the exact final commit and record run URLs/commit SHA below. Any failed or skipped required workflow must be explained; no automatic merge or deployment is authorized by this ledger. |

## Evidence record

Append evidence only after it has actually been produced. Keep local reports, model prompts/answers, secrets, database files, and private host details out of Git.

| Evidence | Value |
|---|---|
| Candidate commit SHA | Not recorded — select and freeze a release candidate first |
| Exact-head test workflow URL | Pending — user confirms all Windows tests passed; run URL/log not supplied |
| Exact-head dependency-audit URL | Pending |
| Exact-head browser E2E URL | Pending |
| Exact-head Windows storage workflow URL | Pending — user confirms all Windows tests passed; run URL/log not supplied |
| Target-host baseline report location (outside repository) | Pending — Windows tests reported successful by project owner on 2026-10-28; report not attached |
| Disposable legacy migration row-preservation / restart / health result | Pending confirmation that the successful Windows run covered all these issue-specific checks |
| Live evaluation report + dataset hash + model digest (local only) | Pending |
| Completed human-adjudication worksheet (local only) | Pending |
| Independent security reviewer and assessment URL | Pending |
| Staging restore/rollback and recovery/soak evidence | Pending |

## Close criteria

- Close issue #296 only after the exact legacy migration is demonstrated on a disposable copy of the affected Windows database, data preservation is verified, and startup/restart plus /api/health succeed on the target host.
- Close issue #52 only after an independent security reviewer records the assessment and required remediations pass retesting.
- Close issue #290 only when every gate is either supported by recorded evidence or explicitly accepted as an intentionally disabled/non-goal by the project owner. A required external prerequisite must remain marked blocked; do not claim full acceptance while it is unresolved.
