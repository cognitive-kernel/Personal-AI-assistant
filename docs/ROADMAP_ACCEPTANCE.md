# Roadmap acceptance status

Last reviewed: 2026-10-21

This document records repository-level evidence and the remaining acceptance gates. A merged implementation or passing CI suite does not by itself establish production readiness on a user's machine.

Repository status last reconciled on 2026-10-20 at `bd4267f069c0b469541ddc0e16303fa83a9bc597`. Task dashboard summary/search ([PR #356](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/356)), safe bulk operations ([PR #357](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/357)), semantic task status styling ([PR #358](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/358)), current-section navigation ([PR #360](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/360)), accessible learning-path progress ([PR #362](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/362)), and contextual task empty states ([PR #363](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/363)) are merged. Post-merge main CI passed on `bd4267f069c0b469541ddc0e16303fa83a9bc597`: [tests and Windows storage regressions](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408060), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408011), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368408061). Target-host, live-model, manual accessibility, staging/rollback, and independent security acceptance remain open.

## Current status

| Stage | Status | Evidence and remaining gate |
|---|---|---|
| Repository audit and baseline | Partial | A redacted target-host baseline tool now records commit/runtime, read-only SQLite health, and optional full pytest counts. The tool still must be run on the actual target host; complete checkout and broader supply-chain audit remain; a scheduled `pip-audit` workflow now checks the resolved Python environment on pull requests, main pushes, and weekly. |
| Module boundaries and contracts | Partial | The merged AST-based audit (`scripts/check_import_cycles.py`) scans 55 Python modules and found no eager module-time import cycles on its checked head. Dynamic/runtime dependencies and architecture-level refactoring still require broader review. |
| Persistent task execution | Implemented with bounded evidence | SQLite queue, leases, checkpoints, cancellation, recovery, thread-race tests, a six-process independent-connection claim test, an abrupt worker-process termination/checkpoint-recovery test, and an isolated concurrent-enqueue/claim-complete benchmark are covered by CI. The allow-listed `TaskRuntime` is wired into FastAPI startup/shutdown; learning jobs are reconciled and enqueued idempotently, and the schema registry validates startup before worker activation. The benchmark now rejects paths aliasing configured application SQLite stores and rejects identical settings/learning paths before initialization. Run `python scripts/benchmark_task_queue.py --tasks 1000 --producers 4 --workers 4 --output reports/task-queue-benchmark.json` on the target host and archive the report. Target-host crash/restart, storage fault injection, sustained load, and recovery across every task type remain. |
| Research and learning | Implemented with limitations | Source-backed research, provenance, source-conflict reporting, local quality diagnostics, learning paths, recommendations, spaced review, advisory semantic review, a deterministic lesson evidence gate, rubric-based partial-credit assessment, and automatic mastery progression exist. The acceptance gate now requires explicit claim-to-test coverage before durable promotion; source doctests and fenced assertions remain scoped evidence, not lesson-wide proof. A versioned ten-case claim-coverage benchmark spans five domains and reports false-acceptance/false-rejection rates for the structural mapping gate. The bilingual answer-quality benchmark reports the rate at which exact reference-listed prohibited phrases would still be accepted; this lexical denominator does not measure semantic false acceptance. Live Ollama evaluation against pinned model and trusted domain-specific factual oracles, larger independently curated factual reference datasets, test semantic-adequacy review, and false-pass calibration remain required. Human scoring is not a runtime acceptance requirement.
| Isolated execution | CI-validated | Docker sandbox and self-improvement test runner use restricted containers; CI includes real Docker integration with a pinned image. Target-machine installation and operational validation remain. |
| Experience memory | Implemented with limitations | Versioned experiences, explicit capture consent, source checks, freshness-aware hybrid retrieval, explicit feedback, and controlled verification exist. A versioned synthetic bilingual retrieval benchmark now has deterministic fail-closed gates for recall@k, MRR, and zero-recall query rate; the benchmark still needs broader independently curated coverage before it can represent general retrieval quality. Runtime human scoring is not part of acceptance. |
| Local operations UI | Partial | Chat/task controls, cursor-paginated task listing, durable task status/priority/schedule audit history (PR #347), interactive cursor-paginated audit history (PR #351), learning, experience review, storage health, backup/restore staging, and self-improvement controls exist. Real Chromium CI covers dashboard loading, storage health, recommendations, experience creation, consent revocation after reload, experience review persistence without implicit verification, backup-to-staging-restore, cancellation of a persisted scheduled task, persisted research-task recovery after reload, self-improvement proposal validation/rejection, explicit confirmation for draft PR/rollback controls (GitHub-facing calls mocked), responsive navigation, skip-to-content, named controls, and overflow at 360/768/1280px. The responsive/accessibility implementation is in [PR #326](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/326), with post-merge accessible-name regression fixes in [PR #327](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/327). Manual screen-reader/keyboard review, broader operational controls, and target-machine usability validation remain. |
| Resource discovery | Read-only by policy | Local hardware detection and cloud resource discovery are available. Provisioning and remote execution intentionally remain disabled until provider authorization, budget limits, auditability, and security acceptance are specified and tested. |
| Governed self-improvement | Controlled proposal workflow implemented | Patch allowlists, isolated syntax and behavioral tests, test-integrity checks, explicit approval, draft PR creation, and draft rollback are available. Automatic merge, deployment, or production mutation remain disabled by design. |
| Security and release hardening | Partial; independent review required | A global loopback-only HTTP boundary blocks non-loopback peers before they reach the UI or API, with route-level guards retained as defense in depth. The target-host baseline now reports hostname-based Ollama endpoints as `unsafe_or_unverified` rather than assuming `localhost` resolves to loopback; it deliberately does not resolve DNS or probe the network. Issue #52 remains open for independent review of URL parsing, DNS/loopback behavior, proxy/redirect handling, validation, limits, and the broader network threat model. A reviewer independent of the implementer must record findings and retest remediations. |



The task dashboard includes a live summary and client-side search bounded to loaded pages; cursor pagination, durable status audit, interactive event history, status filtering, priority/schedule controls, and safe bulk operations are merged. semantic visual status styling with Persian labels are merged. Browser E2E verifies status classes, preserved raw identifiers, localized labels, and accessible names. This UI change does not alter API contracts or task behavior.

Task audit history was made interactively cursor-paginated in [PR #351](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/351), merged at `6f2137e6b60a0cf34361ec733b4b3d64171f1bd9`. Exact-head tests, browser E2E, and dependency audit passed ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324069), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324023), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39278324165)). Post-merge tests and browser E2E on `main` were still running when recorded; do not interpret PR CI as local or target-host acceptance.

## Required acceptance gates before calling the project complete

1. **Target-host baseline and legacy migration:** the reviewed exact-fingerprint, backup-first migration for the reported `learning.db` schema is merged in [PR #306](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/306); its fixture and data-preservation checks passed in CI. Read-only unknown-schema diagnostics and backup-first recovery instructions are now in [PR #327](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/327), including tests for non-mutation and no row-content disclosure. Run the complete test suite, `scripts/inspect_sqlite_schema.py` against the actual target databases, schema/storage health checks, and app startup on the actual Windows target after pulling `main`; verify the generated `learning.db.pre-migration-*.bak`, `/api/health`, and a second restart. Archive redacted outputs and exact commit/dependency versions. Windows-specific migration and rollback CI coverage is added by [PR #310](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/310); CI regression checks are green, but real-target migration/rollback acceptance remains open.
2. **Independent security review:** complete Issue #52, including threat model, reachable network boundaries, DNS/loopback behavior, request validation, and regression tests. Do not self-close this gate.
3. **Live-model quality evaluation:** run the versioned Persian/English evaluation dataset against the intended local model; capture model digest where available; require the automated fail-closed gate for reference-concept coverage, prohibited claims, retrieved-source provenance, claim-review status, and per-case latency. Runtime acceptance does not use human scoring. The gate is a bounded regression signal, not proof of scientific truth.
4. **Operational UI acceptance:** repository CI now includes real Chromium scenarios for research, task cancellation/recovery, consent revocation, experience review, backup preview/restore staging, self-improvement draft workflows, and responsive/accessibility smoke checks. The browser checks pass on the current main commit, but manual keyboard/screen-reader review and target-machine usability acceptance remain required.
5. **Recovery and load evidence:** run `scripts/benchmark_task_queue.py` on the target host using disposable SQLite stores and archive the JSON report; exercise process crash/restart and multi-process contention on disposable data; measure latency, memory, CPU, disk growth, queue recovery, and backup/restore behavior. The target baseline now applies a configurable queue throughput floor (default 10 claim/complete tasks per second) and can optionally measure the configured loopback Ollama model's median request latency and generation tokens/second with a model digest. The thresholds and redacted reports must be retained with the exact target-host commit. These bounded benchmarks do not substitute for sustained-load testing.
6. **Deployment policy:** local install/update/rollback and backup procedures are documented in `docs/operations/INSTALL_UPDATE_ROLLBACK.md`; the target-host rehearsal, including staging restore and rollback, remains outstanding. Keep remote execution/cloud provisioning disabled until a separate security and cost review approves them.

## Additional release gate



7. **Final clean-start and test-data isolation:** all current learning content is disposable and must never be promoted as trusted release knowledge. After all implementation and evaluation gates pass, rehearse initialization using fresh disposable stores, verify the registered schema/migration history, confirm test learning content is absent, and rerun target-host smoke tests. Do not truncate or replace development/live stores to bypass schema errors. Follow [Final clean-start and learning-data acceptance](operations/FINAL_CLEAN_START_ACCEPTANCE.md). This gate remains outstanding until its evidence is attached to the exact release commit.

## Non-goals without a separate approval gate

- Do not auto-apply arbitrary generated patches to the running application.
- Do not auto-merge or deploy self-improvement PRs.
- Do not treat a model's own semantic review as independent verification.
- Do not enable cloud provisioning or remote execution by default.
- Do not replace live databases automatically during backup restoration.

## Evidence policy

Every acceptance claim must link to the exact commit and CI run or target-host report. A skipped test, mock, static check, or successful CI run must not be described as proof of a different environment or of factual model quality.


## Dashboard navigation refinement (2026-10-20)

The persistent section navigation now exposes exactly one current destination using `aria-current="location"`, updates its active item on scroll, resize, and hash changes with a requestAnimationFrame-throttled handler, and uses a responsive active indicator. Browser E2E asserts the initial state and navigation to the task manager. This is repository-level behavior only; manual keyboard/screen-reader and target-host usability acceptance remain required.


### Learning-path progress visualization (2026-10-20)

The learning dashboard derives a bounded progress percentage from completed and total topics and renders a native HTML progress bar with an accessible name, value text, and localized completed/total caption. The percentage is derived from counts rather than trusting a possibly inconsistent stored percentage. Responsive styling uses local CSS only. Exact-head browser CI passed and the feature is merged into current `main` ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788776), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368789105), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788829)); manual keyboard/screen-reader and target-host usability acceptance remain separate gates.


### Task dashboard empty states (2026-10-20)

The task queue now distinguishes a genuinely empty queue from zero matches caused by search/status filters. Filtered empty states expose a clear-filters action; text is created with textContent, no task payload is exposed, and the layout uses local responsive CSS. Browser E2E covers no-match recovery and the truly empty queue. Exact-head CI passed and the feature is merged into current `main` ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788776), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368789105), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368788829)); manual accessibility and target-host usability remain separate gates.


### Dashboard theme preference (2026-10-20)

The dashboard theme preference is implemented and merged in [PR #365](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/365), commit `9702984f236e0c3fdefad16365e1127027e17b87`. Exact-head tests/Windows regressions, browser E2E, and dependency audit passed ([tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288911), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288901), [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39369288902)). OS preference fallback and explicit local persistence are covered. Manual contrast, keyboard/screen-reader, and target-host acceptance remain open.
\n\n## Current draft branch: automated learning acceptance\n\nThe open draft PR [#370](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/370) contains automated lesson evidence gates, rubric-based partial-credit assessment, transfer-question checks, prerequisite enforcement, automated answer-quality acceptance, queue performance thresholds, and an opt-in local Ollama latency/throughput benchmark. The current revision adds exact URL citation-token matching and requires every reviewed claim to cite a source actually cited in the lesson. CI runs are repository-hosted checks, not local-machine acceptance. Do not start or report the user's final local test phase until the remaining programming gaps are closed; three consent-gated Python/JSON sandbox practical exercises now record versioned test outcomes and can update course mastery after prerequisite checks; broader topic/language coverage and versioned transfer/relevance benchmarks remain open.\n

## Learning subsystem failure analysis — 2026-10-21

The latest completed CI run before the current fix reported 695 passed, 3 failed, and 5 skipped in the main test job. All three failures were lesson-generation tests expecting a structurally valid lesson to remain available. The same failing test suite ran first in the Docker integration workflow, so the actual real-Docker integration step was skipped; that workflow failure was not evidence that the sandbox itself failed its integration assertions.

Root cause: one boolean, `quality.accepted`, was used for two different decisions: whether a lesson is usable by the learner and whether its scientific evidence/provenance gate passed. The evidence gate requires two exact cited HTTPS source URLs on distinct hosts, at least one allowlisted primary-source candidate, and a successful model-assisted claim review. A lesson generated from model knowledge without sources can never satisfy those conditions; a lesson with only one source or a non-allowlisted test source also fails. The final gate then changed the lesson's status to `rejected`, even when its lesson shape and assessment had passed. This contradicted the documented fallback that permits a clearly unverified model-knowledge lesson and caused the three CI regressions.

The branch now separates the decisions: structurally accepted lessons remain available for learning, while `scientific_acceptance.accepted` remains false when evidence is insufficient and the knowledge item is stored as `unverified`. Regression assertions cover this split. The retry prompt's required JSON-key list was also corrected to include `answer_rubric`, which it validated later in the same prompt but previously omitted from its explicit required-key list.

This change restores availability; it does not claim scientific truth has been proven. Current evidence gating is a conservative provenance policy plus model-assisted claim review, not an independent factual oracle. Reliable automated factual acceptance still requires versioned, domain-specific reference cases and executable validators where possible. Human scoring is not a runtime requirement, but the absence of human scoring must not be represented as proof that all generated lessons are correct. CI on the exact current branch head is still pending.

## Hybrid learning system — required architecture and acceptance gates (2026-10-21)

The learning subsystem must behave as a staged, evidence-carrying learner rather than a one-shot lesson generator. These are product requirements, not optional future ideas. A source URL or model response must never be rejected solely because the explanation is short or fails a lexical/format heuristic.

### Required end-to-end learning cycle

1. **Capture, do not discard:** accept a topic, pasted text, explicit public URL, or a source discovered online. Store every non-empty candidate in temporary knowledge with source URLs, fetch time, content digest, model/config versions, quality diagnostics, and verification state. Failed retrieval must preserve the candidate and record the retrieval failure; it must not silently substitute an unrelated source.
2. **Extract claims and uses:** split the candidate into atomic claims, definitions, assumptions, boundary conditions, practical applications, and examples. Preserve source span/URL for each claim. Mark claims without traceable source as model-generated hypotheses.
3. **Research the public web:** search multiple available public search engines, fetch public pages safely, deduplicate by canonical URL/content hash, and store supporting, contradicting, context, or application evidence. Store title, URL, retrieval timestamp, digest, excerpt, and reliability notes. Do not treat search ranking or fetch success as proof. Prefer primary documentation, peer-reviewed sources, standards, official specifications, and reproducible datasets where appropriate.
4. **Consult peer AI systems:** when providers are explicitly configured, send each candidate to up to three OpenAI-compatible review endpoints. Ask each to challenge claims, find counterexamples, state limitations, propose applications and falsifiable tests, and cite public URLs. Persist provider/model, exact prompt/response, parsed claims/citations, response digest, and errors. Read secrets from environment variables; never persist them. If no provider is configured or a service is unavailable, continue offline and record that consultation was unavailable. Multiple AI opinions are not independent votes unless independence is established.
5. **Construct an executable verification plan:** combine source-backed examples, deterministic assertions, curated reference datasets, property-based/metamorphic tests where applicable, and sandbox exercises. For code or algorithms, use the isolated sandbox with fixed expected outputs, edge cases, and resource limits. For factual/non-executable claims, require domain-specific reference cases or traceable evidence; a model-generated answer key alone cannot establish correctness. Tests without a trusted expected result remain exploratory and cannot promote the candidate.
6. **Execute and retain outcomes:** run only allowlisted tests in an isolated environment. Save test-plan version, runner/version, expected/actual result, pass/fail, time, logs, source digest, and candidate digest. Convert every failed test into a durable learning experience recording what was tried, expected result, actual result, and next falsifiable hypothesis. Keep failed candidates for revision and retest; do not delete them.
7. **Promote conditionally:** promote a candidate to the main knowledge store only when all required acceptance tests for its declared scope pass and evidence requirements are met. Record scope and verification level. A pass means validated against this versioned test set, not universally or eternally true. If evidence is insufficient, retain the candidate as awaiting_test/unverified; do not label it verified.
8. **Learn from new information:** when later sources, peer responses, model versions, or tests conflict with promoted knowledge, create a new candidate/version and re-run affected tests. Preserve provenance and history; do not silently overwrite prior verified content. Add regression tests for observed failures.
9. **Use established open-source tools where useful:** evaluate property-testing, retrieval and evaluation tools against supported Python version, license, offline behavior, resource limits, and security posture. Pin dependencies and provide deterministic fallback paths. Never use an untrusted model-generated test as its own oracle.
10. **Operational transparency:** expose candidate status, source coverage, web-search status, peer consultation status, test outcomes, failure experiences, and promotion reason in the application/API. Network failures must degrade gracefully. Users must be able to inspect why a candidate was not promoted without manually scoring it.

### Hybrid methods and their roles

- **Retrieval/source-grounded learning:** public URL ingestion, multi-engine discovery, source normalization, deduplication, primary-source preference, and exact provenance.
- **Symbolic/deterministic verification:** fixed expected outputs, schema checks, invariants, unit tests, regression tests, curated answer/reference sets, and explicit acceptance thresholds.
- **Sandbox-based experiential learning:** isolated execution, bounded CPU/memory/time/network, source-derived examples, edge cases, and retained execution reports.
- **Statistical/model-assisted review:** local model for lesson synthesis and claim decomposition; configured peer AIs for adversarial review and test proposals. Model judgments remain advisory unless checked against external oracles.
- **Failure-driven learning:** durable experience records for mismatches, failed tests, missing evidence, source conflicts, and successful repairs.
- **Incremental memory:** temporary candidates, verified knowledge versions, evidence/consultation history, and regression-test corpus remain distinct stores/states.

### Acceptance criteria before declaring the hybrid loop implemented

- Tests prove a concise valid candidate is retained even when lexical heuristics complain.
- The verification plan is versioned, identifies missing trusted oracles and conflicting evidence, proposes next actions, and never fabricates expected outputs or promotes by itself.
- Explicit URL fetch success and failure are both tested; no silent source substitution occurs after a requested URL fails.
- Original-source doctests and safe fenced Python assertion blocks execute only in the bounded no-network sandbox; unsafe or non-asserting blocks are skipped and sandbox success never promotes the whole lesson.
- Web research persists source provenance and treats retrieved pages as context until claim support is checked.
- Peer AI calls are tested with mocked providers, missing keys, timeouts, invalid endpoints, conflicting stances, and secret-redaction assertions.
- Candidate tests cannot pass by omitting expected/actual outputs, changing test order/names, using a model-opinion runner, or passing an empty test list.
- Promotion rejects fabricated pass flags when actual output differs from the oracle, rejects unallowlisted web/source-example oracles, and confines regression fixture references to tests/fixtures without path traversal.
- A failing test leaves the candidate in temporary storage and creates a reproducible failure experience.
- A passing test promotes only within its declared verification scope, with test-set version/digest and provenance attached.
- Unsupported factual claims remain unverified even if several peer AIs agree.
- CI passes on the exact branch head. Target-host performance, clean-start isolation, recovery, and security checks remain separate release gates.

### Current implementation status

The branch has temporary candidates and gated promotion/failure-experience recording, automatic best-effort web research with source-constrained local claim review, configured peer-AI consultation, provenance persistence, user-reported field outcome storage, an explainable evidence score, and a claim-matrix API at `/api/learning/candidates/{id}/claims`. The matrix lists candidate claims, relevant supporting/contradicting/context sources, peer reviews that have lexical relevance to each claim, scoped sandbox test history, real-world feedback, verification channel counts, score breakdown, and a versioned deterministic verification plan. The plan reports available source examples, sandbox readiness, conflicts, missing trusted oracles, blockers, and next actions; it never invents expected outputs or promotes knowledge. Lexical overlap is only a relevance hint and never a truth classifier. An isolated runner executes explicit Python doctest examples and restricted fenced Python blocks with top-level assertions found in the original supplied/retrieved source (not model-generated lesson text) in the configured no-network sandbox and records versioned run history separately from whole-lesson promotion. Fenced blocks with imports, reflective builtins, unsafe constructs, or no directly executed assertion are skipped. A passing example suite contributes limited evidence points but never promotes the entire lesson. One unavailable channel no longer prevents attempts through other channels. The remaining gaps are a curated domain-specific reference-oracle corpus for non-executable factual claims, more precise claim-to-test mapping, and per-source attribution when multiple retrieved pages contain executable examples. Until those gaps are closed and exact-head CI passes, the hybrid learning system must not be described as complete.

### Evidence ledger and confidence score (implemented baseline; calibration remains a release task)

Every candidate must expose **how** it has been checked, not just a single accepted/rejected bit. The evidence ledger separates:

- `internet_sources`: supporting and contradicting sources, distinct hostnames, exact URL and content digest, excerpt and retrieval time;
- `isolated_or_deterministic_tests`: named/versioned tests, trusted oracle reference, expected/actual output, runner and result;
- `peer_ai_consultation`: provider and model, prompt/response digests, stance, cited URLs, and whether independence is known or unknown;
- `real_world_user_feedback`: what the user tried, context, observed output digest, steps, and outcome (`worked`, `failed`, `partial`, `not_tested`).

The baseline confidence index is 0–100 and intentionally explainable: up to 40 points for a complete passing test set, 10 for distinct passing scoped sandbox example suites, 20 for distinct explicitly classified supporting web domains, 10 for supporting configured peer providers, 30 for distinct successful real-world application contexts, and small partial-success credit. Failed scoped suites and real-world outcomes subtract points. Contradicting sources, peer disagreement, and failed real-world applications subtract points. Repeated sources and repeated providers do not stack indefinitely. Context-only fetched pages do not earn support points until explicitly classified.

This is an **evidence-strength heuristic, not a calibrated probability**. Its component weights must be versioned and later calibrated against a held-out benchmark of known true/false claims and observed field outcomes. A score never overrides a failed required test, an unresolved high-impact contradiction, or missing trusted oracle. User-reported success is useful field evidence but is not independently audited. AI agreement can be correlated and must not be counted as multiple independent proofs.

Migration 20 adds durable channel-attempt history for web research, peer-AI consultation, and sandbox execution, including unavailable/unconfigured states; candidate review and the claim matrix expose those attempts so an offline or failed channel is visible rather than silently disappearing. The dashboard now renders the claim matrix beside the evidence score. The application/API should show the score, component breakdown, evidence inventory, contradictions, missing verification channels, and exact verification scope. When no sandbox is available, web evidence plus peer-AI consultation may raise the evidence index and produce `unverified` / `evidence_reviewed_no_lab`; it must not falsely label the claim experimentally verified or make the candidate available as durable knowledge. The candidate remains in temporary storage until a trusted test suite passes.

### Open-source method selection

Use mature open-source tools where they improve the loop rather than rebuilding every evaluator: `pytest` for deterministic regression gates and `Hypothesis` (now a development dependency) for property-based evidence-score invariants. Evaluate `DeepEval` / `Ragas` separately for optional model-assisted and retrieval-augmented generation evaluation. These frameworks measure different properties and their LLM-as-judge scores are not factual oracles. Check license, supported Python versions, offline operation, transitive dependencies, model/provider requirements, resource use, and privacy before adding any runtime dependency. Keep core storage, provenance, and deterministic acceptance runnable without external service credentials.

### Unified hybrid-cycle implementation update (2026-10-21)

`app/hybrid_learning.py` now provides one orchestration entry point for the web, peer-AI, and isolated-sandbox channels. The channels are attempted independently, each channel failure is recorded without suppressing the others, and the result returns the evidence review and claim matrix. Learning jobs and the candidate research API use this same orchestrator. A failed explicitly requested URL is recorded as unavailable and does not silently trigger unrelated web discovery; peer-AI consultation and any available source-example sandbox tests still proceed. The orchestrator itself never promotes knowledge.

Automated tests cover independent channel failures, explicit-URL failure without source substitution, and disabled web research while peer-AI/sandbox checks continue. This is an integration improvement, not closure of the remaining factual-oracle/calibration gaps. Exact-head CI must pass before the change is considered validated.

Peer-AI review responses now expose their proposed tests, applications, and limitations in the claim matrix. These remain explicitly marked `exploratory_only`, `executed=false`, and `trusted_oracle=false`; they are surfaced for the next verification step but cannot be promoted or run as trusted acceptance tests without an independently established expected result.

### Narrow automatic promotion path (claim-scoped)

Source-authored doctests and restricted assertion blocks are executed in the no-network sandbox and recorded as **scoped evidence only**. A passing example suite does not promote the whole lesson. The durable-knowledge promotion function now requires every claim extracted from the immutable candidate content to be explicitly mapped to at least one uniquely named test, as well as all trusted-oracle, runner, expected-output-hash, and exact expected/actual checks. An incomplete or unknown claim mapping keeps the candidate in awaiting_test and writes a coverage-block event; an actual failed test remains a failed candidate with a preserved failure experience.

Claim IDs are shared between the review matrix and promotion gate. Coverage completeness is structural only: it does not independently establish that a test semantically proves its assigned claim. The review matrix therefore continues to expose claim text, source relevance and review status rather than calling lexical overlap proof.

### Learning evidence acceptance hardening (2026-10-21)

The consolidated follow-up changes the lesson gate contract to `lesson-evidence-v3`. Every accepted claim must contain non-empty claim text, a non-empty rationale, and exact evidence URLs present in both the retrieved evidence inventory and the lesson's citations. The result reports total claims and explicitly mapped claims; missing attribution fails closed. This establishes traceability only and does not prove logical entailment or scientific truth.

The scoped sandbox runner now requires a source URL from the existing curated primary-source allowlist. HTTPS alone is insufficient: lookalike domains such as `python.org.attacker.example` are rejected before sandbox execution and before a test-run record can be written. Candidates remain temporary when no qualifying oracle exists.

Regression tests were added for missing claim text/rationale and for lookalike oracle rejection. Exact-head CI is the validation authority; until it completes, these tests are added but not yet confirmed passing.

Remaining gates include durable URL attribution for each example extracted from multiple retrieved pages, mapping each example to the precise claim it tests, target-host execution, live-model evaluation against versioned domain-specific trusted reference cases, false-pass/false-fail calibration, and independent security review. Human scoring is not a runtime acceptance requirement. Do not mark the project production-ready based on these changes alone.


The source-example runner also fails closed when a candidate lists multiple trusted source URLs but the stored executable examples are not individually attributable to one source. It returns `source_attribution_ambiguous` and records no passing test run rather than assigning a combined suite to an arbitrary URL. Full multi-page support still requires storing and running a separate example suite per retrieved URL and mapping each suite to its claims.

### Evidence-only review without promotion

Evidence from multiple primary-source candidates and configured peer-AI providers can improve diagnostics and the evidence-strength index, but it cannot promote a candidate into the durable knowledge table. Peer-provider names do not establish independent model judgments, and URL allowlisting does not prove that a source entails a claim. Without a trusted, passing test suite for the declared scope, the candidate remains in temporary storage with status `awaiting_test` and an explicit `evidence_reviewed_no_lab` review signal. Contradictory evidence blocks promotion and remains visible for retesting. Only the versioned trusted-test gate may promote knowledge.


## Dashboard navigation and page separation — 2026-10-21

The dashboard's main route must remain a concise overview. Every operational area must have a dedicated URL and a distinct HTML response containing only that area's markup (plus the shared navigation/header and critical storage warning). CSS-only hiding of unrelated sections is explicitly insufficient because hidden content still ships in the document and remains confusing to users.

Required routes include storage health/backups, chat/research, learning/evidence review, learning paths, durable knowledge, task management, experience memory, cloud resource discovery, cloud planning, Kaggle quota, OCI limits, sandbox, self-improvement, and settings.

Acceptance criteria:
- `/` contains overview/navigation but no operational section forms or data panels.
- Each `/sections/{slug}` response includes its intended section and excludes the markup for every unrelated section.
- All navigation links target their dedicated routes and the active route is accessible via aria-current="page".
- Shared scripts tolerate absent section controls and do not initialize unrelated subsystem APIs on every page.
- Browser E2E exercises representative interactions on the corresponding page, and CI passes on the exact branch head.


### Claim-level acceptance, benchmark measurement and security boundary — 2026-10-21

- app/claim_test_coverage.py derives stable claim IDs shared with the verification matrix and requires an explicit mapping from each extracted claim to at least one planned test before promotion.
- Fully passing tests with missing/unknown claim mappings leave the candidate in awaiting_test; the mapping and blockers are persisted as an evidence event. Real failed test runs still produce a failure experience.
- benchmarks/knowledge_acceptance/v1/cases.jsonl provides deterministic structural-policy cases across software security, source provenance, data quality, model evaluation and runtime behavior. scripts/run_knowledge_acceptance_eval.py reports false acceptance and false rejection rates and a dataset SHA-256.
- The bilingual answer-quality evaluation now reports a false-acceptance rate for outputs containing exact prohibited phrases from the versioned reference cases. This is a narrow measurable lower bound, not a semantic error rate.
- CI exercises the code and sandbox integration; live model evaluation and target-host behavior have not been performed here. Independent security review, broader trusted factual oracles, and semantic review that verifies the relevance of every test-to-claim mapping remain release gates.
