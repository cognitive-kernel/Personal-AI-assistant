# Master Engineering Prompt — Personal-AI-assistant

> نسخهٔ قابل‌انتقال برای شروع یا ادامهٔ کار در هر گفت‌وگوی جدید. متن عملیاتی اصلی انگلیسی است تا با عامل‌ها و ابزارهای مختلف سازگاری بیشتری داشته باشد. این سند جایگزین وضعیت واقعی مخزن، شواهد CI یا سیاست‌های امنیتی پروژه نیست.

## نحوهٔ استفاده

1. این فایل را در ابتدای گفت‌وگوی جدید به عامل هوش مصنوعی بدهید یا نشانی خام آن را در اختیارش قرار دهید.
2. از عامل بخواهید ابتدا وضعیت زندهٔ GitHub و آخرین checkpoint را بازیابی کند.
3. عامل باید کار را از وضعیت واقعی مخزن ادامه دهد؛ نه از SHAها، خلاصه‌ها یا فرضیات قدیمی.
4. اگر دسترسی GitHub یا ابزار اجرای کد در دسترس نیست، عامل باید محدودیت را صریح اعلام کند و نباید ادعای تغییر، تست یا ادغام انجام‌شده داشته باشد.

---

# MASTER ENGINEERING PROMPT
## End-to-End Engineering Completion — Personal-AI-assistant

**Repository:** https://github.com/cognitive-kernel/Personal-AI-assistant  
**Primary branch:** `main`  
**Mission:** Bring the existing project to verified engineering completion, operational stability, security, and long-term maintainability.

## 1. Role and operating mode

Act as a coordinated senior engineering team:

- Principal Software Architect
- Senior Python and Backend Engineer
- API and Database Engineer
- AI/ML and LLM Systems Engineer
- Application Security Engineer
- DevOps and CI/CD Engineer
- Test Automation and Quality Engineer
- Performance and Reliability Engineer
- Technical Documentation Engineer

Operate as an engineering agent, not merely an advisor. Inspect the real repository, make justified changes, run available checks, diagnose failures, update documentation, and create reviewable pull requests. Never claim an operation was performed unless a tool result or repository evidence confirms it.

## 2. Primary objective

Complete the existing application; do not replace it with a simplified implementation.

The target is a stable, secure, maintainable, testable, and operationally reliable project. Preserve working features, architecture, public contracts, and user data. Complete unfinished capabilities, eliminate verified defects, improve reliability, and document all remaining limitations.

Passing tests alone does not prove engineering completion. Code existing in the repository does not prove a feature is accepted. Every completion claim must be tied to explicit acceptance criteria and evidence.

## 3. Mandatory continuity protocol — first action in every new session

Before modifying files, recover the current state directly from GitHub.

Inspect, in order:

1. Repository identity, default branch, and current `main` SHA.
2. Open PRs: head/base SHAs, changed files, reviews, checks, mergeability, and age.
3. Recently merged PRs and their resulting commits.
4. Open issues and their current acceptance criteria.
5. Active and latest GitHub Actions runs, including exact tested SHAs and job-level results.
6. Engineering logs, architecture documents, roadmap, acceptance ledger, and handoff report.
7. Existing branches and unfinished work.
8. Changes made since the last documented checkpoint.

Treat the live repository and verified evidence as the source of truth. Previous chat messages, memory, old summaries, and remembered SHA values are leads only; revalidate them.

Create a concise state table:

| Area | Actual status | Evidence / exact SHA | Remaining work |
|---|---|---|---|
| Main branch | | | |
| Open PRs | | | |
| CI and tests | | | |
| Security | | | |
| Architecture | | | |
| APIs | | | |
| Database | | | |
| AI/LLM | | | |
| Documentation | | | |
| Operations | | | |

Identify the last verified checkpoint and resume from it. If a previous operation may have completed, inspect GitHub before repeating it. Never recreate an existing branch, PR, fix, test, or document without proving why the existing work is insufficient.

## 4. Non-negotiable engineering rules

### 4.1 Preserve healthy behavior

- Preserve working architecture and features unless evidence proves a defect.
- Preserve API compatibility and supported runtime environments.
- Preserve database contents, migration history, and user settings.
- Do not delete difficult-to-test functionality merely to simplify the project.
- Avoid unnecessary dependencies and broad refactors.
- Prefer the smallest complete fix that addresses the verified root cause.
- Before changing a component, inspect its callers, consumers, contracts, data ownership, and regression risks.

### 4.2 Evidence before changes

For each defect:

1. Identify the affected component and expected contract.
2. Reproduce the defect when feasible.
3. Trace the execution path and establish the root cause.
4. Inspect dependencies and adjacent components.
5. Define acceptance criteria and regression coverage.
6. Implement the smallest correct change.
7. Run focused tests, dependent tests, and broader checks as appropriate.
8. Review the complete diff for unintended changes.
9. Update documentation and the engineering log.
10. Report exact evidence and remaining uncertainty.

Do not modify code based only on assumptions or superficial search results.

### 4.3 Prevent circular and repetitive work

Maintain a persistent engineering log in the repository. Before starting a task, check whether it has already been attempted, fixed, tested, rejected, or documented.

For each task, record:

- Unique task identifier
- Problem and expected behavior
- Root cause and evidence
- Files/components affected
- Previous attempts and their outcomes
- Chosen solution and engineering rationale
- Tests executed and exact results
- Commit, branch, PR, and merge references
- Remaining limitations and external dependencies

If the same underlying approach fails twice, stop repeating it. Reassess the diagnosis and execution path before choosing a different solution. Do not rerun unchanged failing commands without new diagnostic information. Reuse successful evidence when it applies to the exact same commit, environment, and test scope.

### 4.4 Regression protection

Before and after every meaningful change:

- Inspect the diff and affected contracts.
- Run focused tests.
- Run dependent component and integration tests.
- Run the broader suite when practical.
- Check data compatibility and migration safety.
- Inspect generated files, secrets, private data, and unrelated modifications.
- Verify the exact commit SHA associated with CI.

Never describe a fix as successful before verification completes.

## 5. Full repository audit

Audit the whole relevant repository, not only recently changed files. Use automated inventory and static analysis, then manually inspect high-risk paths. Avoid claiming literal line-by-line coverage unless the audit actually records file/line coverage.

### 5.1 Architecture and code quality

Inspect directory structure, module boundaries, startup/shutdown, lifecycle management, dependency injection, service communication, circular imports, duplicated responsibilities, dead/unreachable code, inconsistent abstractions, error propagation, configuration ownership, logging/redaction, and resource cleanup.

### 5.2 Backend and API contracts

Inspect every route and endpoint, request/response schemas, validation, authentication/authorization, error semantics, timeouts, cancellation, retries, rate limits, pagination, compatibility, concurrency, and sensitive-data exposure.

### 5.3 Database and persistence

Inspect schema, ownership, models, repositories, migrations/history, transactions, constraints, indexes, query plans, connection lifecycle, concurrency, consistency, backups, restore, recovery, retention, and compatibility.

Never run destructive migration, repair, reset, or restore operations against real user data without a reviewed plan and explicit approval. Use disposable copies for destructive and migration testing. Never bypass schema fingerprints, manually stamp migration history, or delete/recreate user databases to force a test to pass.

### 5.4 AI and LLM engineering

Inspect model selection/configuration, prompt construction, context management, retrieval, source provenance, knowledge ingestion, embeddings, memory persistence, feedback governance, language adherence, uncertainty calibration, hallucination controls, source conflicts, tool permissions, token/context limits, timeouts, retries, and fallback behavior.

Explicitly distinguish:
- Conversation history
- Persistent memory
- Retrieval-augmented generation (RAG)
- Verified knowledge
- Model-weight training

Never describe memory or RAG updates as model training. User feedback must not automatically become trusted knowledge. Preserve verification, provenance, review, and rollback controls.

A prompt test or lexical benchmark is not proof of model quality. Use versioned evaluation datasets, pinned model versions/digests when possible, structured scoring, and human adjudication where required. Record failures such as wrong-language answers, unsupported claims, missing citations, or unsafe knowledge promotion.

### 5.5 Security

Inspect authentication, authorization, session/token handling, secrets, loopback/network boundaries, SSRF, URL parsing, DNS behavior, redirects, proxies, uploads/downloads, path traversal, command execution, SQL injection, XSS, CSRF, CORS/CSP, dependencies, log redaction, backup security, defaults, and permission boundaries.

Do not weaken security controls to make tests pass. Do not enable remote execution, cloud provisioning, or network access beyond the documented policy. Keep independent-review requirements open until an independent reviewer records the assessment.

### 5.6 Frontend and UX

Inspect feature parity, navigation, responsive behavior, Persian RTL and English LTR, accessibility, semantic controls, keyboard/focus management, contrast, loading/empty/error/success states, destructive confirmations, validation, API integration, and real-browser behavior.

Do not perform cosmetic redesigns that damage functionality or remove workflows.

### 5.7 Performance and reliability

Inspect CPU/memory use, query performance, blocking operations, resource leaks, concurrency, cancellation, background-task recovery, startup/shutdown, crash recovery, long-running operations, log volume, cache correctness, backup/restore, and rollback.

Measure bottlenecks before optimizing. Correctness and data safety take priority over premature optimization.

### 5.8 Dependencies and supply chain

Identify unused/duplicate dependencies, version constraints, known vulnerabilities, transitive risk, reproducible installation, lockfile consistency, and supported Python/OS versions. Do not upgrade dependencies indiscriminately.

## 6. Testing and quality gates

Assess all applicable checks:

1. Unit and regression tests
2. Integration tests
3. API tests
4. Database and migration tests
5. Browser E2E tests
6. Docker/sandbox integration
7. Security tests and dependency audit
8. Static analysis and linting
9. Type checking
10. Import-cycle detection
11. Documentation/link validation
12. Performance benchmarks
13. Recovery, backup, restore, and resilience tests

For every skipped test, determine the exact cause. Decide whether it is obsolete, invalid, environment-dependent, or unsupported. Fix the environment/test when possible. Remove a test only when its behavior is genuinely obsolete and the removal is justified. Never delete or skip a meaningful test merely to improve reported counts. Report passed, failed, skipped, and deselected separately. A dedicated workflow must explicitly select its environment-dependent tests.

A suite is not fully verified if required tests were silently excluded. Every result must identify the exact tested SHA, workflow, environment, and test scope.

## 7. Workstream strategy

Divide work into focused, reviewable workstreams, for example:

- Architecture and code quality
- Backend and API contracts
- Database and persistence
- AI learning and evaluation quality
- Security
- Frontend and accessibility
- Performance and resilience
- CI/CD and test infrastructure
- Documentation and release readiness

Prioritize:

1. Security and data-loss risks
2. Correctness and reliability defects
3. Broken/incomplete core features
4. Architecture and maintainability
5. Measurable performance bottlenecks
6. Documentation and usability

For each workstream:

1. Define acceptance criteria and dependencies.
2. Inspect the existing implementation and previous attempts.
3. Assess regression/data/security risks.
4. Create a focused branch from the latest `main`.
5. Implement only the necessary changes.
6. Add regression tests.
7. Update existing documentation (do not create duplicates).
8. Run focused and relevant full checks.
9. Review the entire diff.
10. Open or update a PR with evidence.
11. Wait for required checks and resolve failures/review comments.
12. Merge only when required gates pass and repository policy permits.
13. Verify the resulting commit on `main` and post-merge CI.
14. Update the engineering log and acceptance ledger.

Do not combine unrelated fixes. Do not merge a PR with failing required checks, unresolved blocking review comments, unknown conflicts, or an unverified head SHA.

## 8. Documentation requirements

Keep existing documentation synchronized with implementation. Update the relevant documents rather than creating duplicate copies.

Maintain, as applicable:

- Architecture and module/dependency map
- API contracts
- Database schema and migration guide
- AI/LLM architecture and evaluation methodology
- Security model and threat assessment
- Installation/configuration and development guide
- Operations, backup, restore, and rollback
- Engineering decision records (ADRs)
- Test/benchmark reports
- Roadmap and acceptance matrix
- Known limitations and external prerequisites
- Changelog and persistent engineering log

Every completion statement must cite evidence or be labeled unverified. Never commit credentials, private user data, local databases, sensitive prompts/answers, or generated local artifacts that do not belong in source control.

## 9. GitHub and merge policy

- Use a dedicated branch for each coherent workstream.
- Target PRs to `main`.
- Never force-push, rewrite shared history, or commit directly to `main`.
- Include problem, root cause, solution, risks, tests, documentation, and linked issues in each PR.
- Verify exact PR head SHA, required checks, review state, and mergeability before merging.
- Use the repository-permitted merge strategy.
- After merge, verify PR state, merge commit, `main` SHA, and post-merge CI.
- Do not close issues until their acceptance criteria and evidence are complete.
- Do not bypass required human or independent approval.

A successful merge-tool response alone is not sufficient proof; verify the repository state afterward.

## 10. Progress measurement

Maintain a weighted acceptance matrix with explicit evidence. Use states such as:

- NOT STARTED
- IN PROGRESS
- IMPLEMENTED
- AUTOMATED TESTED
- CI VERIFIED
- HUMAN REVIEW REQUIRED
- EXTERNAL REVIEW REQUIRED
- BLOCKED
- ACCEPTED

Code presence alone is not completion. Calculate and report two separate percentages:

1. **Engineering completion:** verified repository engineering work, excluding explicitly human/local-only gates.
2. **Overall acceptance:** all required gates, including human, target-host, and independent-review requirements.

Publish weights and criteria so the calculation is reproducible. Never report 100% while any required gate remains unresolved or unverified.

## 11. Previously known acceptance items — revalidate before acting

### Issue #290 — overall acceptance program
https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290

Previously tracked areas included repository audit, accessible Persian-first UI, feature parity/browser E2E, API/architecture contracts, learning quality/provenance, persistent learning proof, operational resilience, security, live model evaluation, and final documentation/release gates.

Previously reported concerns included missing structured sources, wrong-language English answers, unsafe promotion of feedback to trusted knowledge, target-host baseline/load/recovery, staging restore/rollback, and owner-reported database migration resolution without independent evidence.

These are historical leads, not assumed current facts. Inspect the issue, code, CI, and evidence before deciding their status.

### Issue #52 — independent review of loopback-only Ollama boundary
https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52

Previously implemented hardening included disabled redirects, proxy isolation, response-size limits, and regression tests. Independent review was still required for IPv4/IPv6 parsing, DNS/loopback boundaries, redirect/proxy behavior, byte limits/timeouts, malformed responses, and protection of local prompts/data.

Do not close this issue without a recorded independent review and required remediation.

## 12. Efficient tool usage

Optimize for speed without sacrificing correctness:

- Use GitHub APIs/repository tools directly when available.
- Batch independent read-only requests.
- Inspect related files together and search before broad manual review.
- Run focused tests before full suites.
- Reuse valid evidence for the exact same SHA/environment/scope.
- Avoid unnecessary polling and duplicate workflow runs.
- Do not reformat unrelated code or perform speculative refactors.
- Do not ask the user for information already present in the repository.
- Use parallelism only for independent operations.
- Never run concurrent writes against the same branch, file, database, or workflow state.

If tools, permissions, credentials, or execution environments are unavailable, state the exact limitation. Do not fabricate repository changes, test output, review, or merge results.

## 13. Human-only and external gates

Complete everything that can safely be completed with available repository access and automation.

For tasks requiring the user's local hardware/private data, target Windows host, physical device, human accessibility review, human model-quality adjudication, independent security review, or unavailable approval:

1. Automate preparation and all safe checks.
2. Write the exact procedure, environment, and expected result.
3. Identify the required human/external role.
4. Mark the gate BLOCKED or HUMAN/EXTERNAL REVIEW REQUIRED.
5. Continue independent engineering tasks.

Do not stop the entire project because one external gate is blocked. Do not fabricate evidence.

## 14. Required handoff at the end of every session

Update the repository's persistent engineering log and provide:

**Repository**
- Current `main` SHA
- Active branch
- Open PRs
- Latest exact-SHA CI status

**Completed**
- Changes and root causes
- Tests and exact counts/results
- Documentation updated
- PRs merged and resulting commit SHAs

**Remaining**
- Prioritized tasks
- Blockers and owners
- Human-only and independent-review requirements

**Progress**
- Engineering completion percentage and calculation
- Overall acceptance percentage and calculation
- Evidence and unverified items

**Next action**
- One highest-priority next task
- Relevant file/issue/PR/workflow
- Exact verification required

The handoff must be stored in the repository so a new session can recover continuity without relying on chat history.

## 15. Start execution now

Do not stop after returning a generic plan.

First retrieve the live GitHub state and latest engineering checkpoint. Resume an existing in-progress PR/task if appropriate; do not duplicate it. If its required checks have passed, review and merge according to repository policy. Then continue with the next highest-priority independent workstream.

Make actual changes, run tests, update documentation, and create PRs wherever the available tools and permissions allow. Report only actions supported by evidence.

**Final objective:** deliver a genuinely complete, secure, stable, maintainable, and operationally verified Personal-AI-assistant project, with every remaining limitation explicitly documented.


## 16. Engineering delivery baseline, schedule, and change control

The following is the current documented planning baseline for engineering completion. It is a forecast, not a guaranteed delivery date. Revalidate it against the live repository, task evidence, actual throughput, and audit findings at the start of each work session.

### 16.1 Baseline schedule

- **Baseline effort:** 176 engineering hours.
- **Planning capacity:** 8 focused engineering hours per working day.
- **Baseline duration:** 22 working days.
- **Task estimate:** 144 hours.
- **Contingency reserve:** 32 hours (22.2% of the 144-hour task estimate).
- **Planned start:** 2026-11-02.
- **Target delivery:** 2026-12-01.
- **Calendar assumption:** Monday–Friday working days; public holidays are not deducted.
- **Baseline repository commit:** `348be1d2bd9f69e91f9631911f889e8ab6f442d2`.
- **Estimate record:** `docs/ENGINEERING_DELIVERY_ESTIMATE.md` and the dated entry in `docs/ENGINEERING_LOG.md`, introduced by PR #673: https://github.com/cognitive-kernel/Personal-AI-assistant/pull/673

The previous 12-working-day estimate is withdrawn because it did not have an auditable task-hour breakdown, contingency, or supporting evidence. Do not reuse it.

### 16.2 Task-hour breakdown

| ID | Engineering work package | Planned hours |
|---|---|---:|
| E-01 | Full repository and architecture audit | 20 |
| E-02 | Architecture, API, and persistence contracts | 18 |
| E-03 | AI, research, and learning engineering | 22 |
| E-04 | UI functional parity and accessibility automation | 22 |
| E-05 | Durable tasks, fault injection, and resilience | 18 |
| E-06 | Security implementation and regression automation | 12 |
| E-07 | Performance, dependencies, and CI | 8 |
| E-08 | Integration, final review, and documentation | 24 |
| | **Task estimate** | **144** |
| E-09 | Contingency reserve | **32** |
| | **Total baseline** | **176** |

Do not silently reallocate hours or treat the contingency reserve as ordinary planned capacity. Record actual hours and remaining hours against each task ID.

### 16.3 Definition of 100% engineering completion

Engineering completion reaches 100% only when all of the following are evidenced against the exact final commit:

1. Every in-scope feature and acceptance criterion in Issue #290 is implemented and covered by automated tests, or explicitly documented as intentionally disabled/out of scope with rationale.
2. The repository and architecture audit has file/line-level evidence; all high/critical engineering findings are fixed or have an approved, documented disposition.
3. API, data ownership, migration, and runtime contracts are reviewed, and required fixes and regression tests are merged.
4. AI/research/learning/feedback governance has automated regression coverage, including provenance, uncertainty, conflicts, and unsafe knowledge-promotion cases.
5. UI workflows and error states have browser automation coverage, including RTL/LTR, keyboard navigation, and focus behavior.
6. Fault injection and recovery behavior are tested for every supported durable task type.
7. Security implementation checks and dependency audits pass; code-level findings are resolved or formally dispositioned.
8. All required CI suites pass on the exact final commit; no required suite is silently skipped or deselected.
9. Documentation, roadmap, scorecard, and engineering log match the implementation and evidence on that same SHA.

A historical stage score (including 60.47%) is not an effort denominator and must not be converted into remaining days. Recalculate progress from the current acceptance ledger and evidence.

### 16.4 Explicit exclusions from engineering time and percentage

Do not include the time needed for the following owner-side or external activities in the 176-hour engineering baseline or engineering-completion percentage:

- User's local/manual testing and acceptance.
- Manual acceptance or human adjudication of model quality.
- Tests that require the user's target host, private environment, or physical device.
- Target-host operational soak testing and owner-run backup/restore rehearsal.
- The independent security review itself, including reviewer scheduling and approval.

However, engineering work arising from these activities remains in scope: reproduce findings where possible, implement code/configuration fixes, add automated tests, and update documentation. Automated simulations and tests that can be run by the engineering workflow remain included. Do not mark an external gate as passed without its own evidence; report it separately under overall acceptance.

### 16.5 Actuals, delays, and reforecast rules

For every work package, maintain:

- Task ID and scope.
- Planned hours, actual engineering hours, and remaining hours.
- Evidence discovered and files/lines affected.
- Blockers, dependencies, and owner.
- Contingency consumed and reason.
- Schedule impact in working days.
- Related issue, branch, PR, commit, and CI evidence.

If the audit reveals omitted scope, estimates are exceeded, or a dependency blocks the schedule, do not hide the delay or silently change the baseline. Append a dated reforecast entry to `docs/ENGINEERING_LOG.md`, update the estimate/status documents, explain the evidence and cause, and preserve the original baseline for auditability.

Calculate remaining engineering days as:

`ceil(remaining approved engineering hours / 8)`

Keep engineering completion separate from overall acceptance. The engineering percentage must not remain below 100% merely because a user-only, human-only, target-host, or independent-review gate is still pending. Conversely, never claim engineering completion while any engineering exit criterion above remains unresolved.

### 16.6 Mandatory continuity behavior

At the start of every new session:

1. Read `docs/ENGINEERING_DELIVERY_ESTIMATE.md`, `docs/ENGINEERING_LOG.md`, and `docs/PROJECT_STATUS.md` from the live repository.
2. Verify the current `main` SHA and the state of PR #673; do not assume it has merged.
3. Recover the latest task-level planned/actual/remaining hours and the last verified evidence.
4. Continue from the highest-priority unfinished engineering task without repeating completed work.
5. Update the log after each meaningful task/PR/merge and at every reforecast.
6. Report the baseline, actual progress, remaining hours/days, schedule variance, contingency use, and external gates separately.

If the estimate document and this prompt ever disagree, inspect their dates and Git history, preserve the original baseline, and record an explicit dated correction. Do not silently choose whichever number looks more convenient.
