# Self-Correcting Learning System — Canonical Specification

> Status: design specification. A checked item in the roadmap means code and tests have been verified, not merely documented.

## 1. Scope and rollout

- Begin verification with Python, while allowing claim records for an explicit allowlist of technical domains. Domain recognition is conservative: ambiguous or unknown topics are skipped rather than assigned a guessed environment.
- Use the existing SQLite learning database and durable task queue; do not create a parallel database or queue.
- The initial factual-knowledge slice consists of `knowledge`, `evidence`, and `tests` records. Add consensus, experiences, and advanced metadata only when the first slice is validated.
- Existing lesson-generation records (`learning_jobs`, `knowledge_items`) serve the current educational workflow. Do not silently reinterpret or migrate them as factual claims.
- A model-assisted claim review may persist a domain-scoped claim and source provenance, but always leaves it at `L0_raw/unverified`; topic classification and an official-looking hostname never establish truth or authorize retrieval as verified knowledge.
- `environment_name` is part of claim identity. Python uses the runtime version; other supported domains use a version explicitly named in the topic when recognized, otherwise `unspecified` with that limitation recorded in scope. Ambiguous multi-domain topics are not persisted.
- All schema changes must be versioned migrations, preserve existing data, and pass fresh-install plus legacy-upgrade tests.

## 2. Evidence and lifecycle are separate axes

Evidence level describes what supports a claim:
- `L0_raw`: extracted, not yet evaluated.
- `L1_sourced`: at least one traceable primary-source excerpt supports the claim; model opinions alone are insufficient.
- `L2_tested`: L1 plus a successful, reproducible test applicable to the stated environment/version.
- `L3_human_verified`: explicitly reviewed by a human. This is an additional review marker, not proof that later contradictory evidence can be ignored.

Lifecycle status describes whether a claim may be used:
- `unverified`, `active`, `conflicting`, `failed`, `superseded`, `needs_review`.

Never encode `superseded` as an evidence level. A failed test must first be classified as a claim failure, environment failure, flaky test, or inconclusive result; a single infrastructure failure does not prove the claim false. New contradictory evidence must invalidate normal use until reevaluation, even if the claim was previously human-reviewed.

## 3. Enforced transitions

Application code owns all transitions; an LLM may return a structured judgment but may not choose or persist a lifecycle transition.

Allowed baseline transitions:
- `unverified -> active` only after source/evidence checks satisfy L1.
- `active -> active` with evidence level L2 only after a valid test passes.
- `unverified|active -> conflicting` when material disagreement or contradictory evidence is recorded.
- `unverified|active|conflicting -> failed` only after a reproducible claim failure is established; environment/test-infrastructure failures instead use `needs_review`.
- `active|failed|conflicting -> superseded` only when a replacement claim is linked and validated.
- `failed|conflicting|needs_review -> active` only through an explicit reevaluation with new supporting evidence and required tests.

No direct L0-to-L2 or L0-to-L3 transition. Validate every transition in a domain service and enforce invariants with SQLite CHECK/trigger constraints where feasible. Use transactions so status and evidence cannot be updated inconsistently. Preserve history; do not physically delete knowledge as part of correction.

## 3.1 Immutable factual-claim revision history

Each factual-knowledge record has an append-only `factual_claim_revisions` history for content identity fields (claim text, normalized text, hash, scope, and environment). Migration 17 seeds existing records as revision 1 and triggers record later content changes as new revisions. Migration 18 adds `domain_name`, backfills it from `environment_name`, and includes it in revision history. Lifecycle/evidence-status changes do not create content revisions. This history records changes; it does not itself increase trust or authorize activation.

## 4. Relational knowledge graph

Represent relations in a separate table rather than one `alternative_knowledge_id` field:
- `supersedes`: the newer claim replaces the older claim.
- `contradicts`: claims cannot both be true under the same scope/version.
- `depends_on`: one claim requires another claim to hold.

Relations need foreign keys, a uniqueness constraint, a no-self-link check, timestamps, and validation of relation direction. Do not assume every contradiction is global: store applicability/version scope and the evidence supporting the relation.

## 5. Evidence and test contracts

Each evidence record should retain source URL/type, a bounded excerpt or content hash, capture time, applicability/version, and verification method. For L1, require a specific source excerpt or reference that supports the claim; model votes are not source evidence.

Each test record should include test type, input/command, expected and actual result, exit status, environment/runtime version, start/end time, outcome classification, and bounded logs. Test definitions should be reviewed or derived from an independent oracle where possible; do not let one model author both a claim and its only proof.

L2 applies only when a domain has a reproducible verification method. Do not assign L2 to vague `documentation_check` or `practical_usage` labels without explicit pass criteria. If a domain cannot be tested, keep its evidence level honest and mark it for a separately defined verification policy.

## 6. Security and execution

- Treat fetched web pages, user text, and model output as untrusted data, never as executable instructions.
- Run code only in the existing isolated sandbox with no network by default and strict CPU, memory, process, output, and wall-clock limits.
- Never execute source-provided shell commands directly on the host.
- Record sandbox image/runtime versions so results are reproducible.
- High-risk claims/actions (destructive shell, firewall, credentials, production changes) require explicit human review before being recommended as executable instructions.

## 7. Freshness and scope

Store `verified_at`, `applies_to` (language/runtime/tool version, OS, and relevant configuration), and a re-verification policy. A version-specific test does not prove compatibility with future versions. Changed or unavailable sources trigger review rather than silently preserving a fresh status.

## 8. Cost, queue, and cache

- Prioritize claims that are retrieved or used, rather than evaluating every extracted claim immediately.
- Reuse cached evaluations only when the normalized claim, source hash, prompt version, model/version, and applicability scope match.
- Run consensus and verification in the durable background task queue; chat requests must not wait synchronously for multi-model reviews.
- Apply bounded concurrency, per-job timeout, retry ceilings, and explicit cost budgets.
- Begin with the minimum evidence needed. Request a second independent model only when risk, uncertainty, or expected use justifies the cost. Three correlated model votes are not independent proof.
- Limit self-correction attempts and stop with `needs_review` when the budget is exhausted.

## 9. Success criteria

Measure against a small human-adjudicated golden set. Track factual precision, false acceptance rate, calibration (later failures among L2 claims), freshness, queue latency, cost per accepted claim, and high-risk policy violations. Do not optimize only for fewer `failed` records.

## 10. Implementation checklist

The following are implementation gates, not promises. Mark them complete only after code review and relevant tests pass:
- [x] Canonical specification and generated prompts are generated from `docs/learning/learning-artifacts.json`; CI checks for drift.
- [x] Domain-scoped factual-knowledge records with SQLite `factual_knowledge`, `factual_evidence`, and `factual_tests` tables; claim identity includes `environment_name` and `environment_version`, immutable content-revision history is added in migration 17, and migration 18 introduces canonical `domain_name` with backfill. Automatic trust promotion remains disabled.
- [x] State transition service rejects invalid trust/lifecycle transitions.
- [x] Database constraints cover status enums, evidence-level invariants, foreign keys, and relation uniqueness.
- [x] Knowledge relation table supports `supersedes`, `contradicts`, and `depends_on`.
- [ ] Sandbox verification is bounded and has no network by default. The initial factual-claim runner accepts only active `L1_sourced` Python claims, requires explicit sandbox consent and a human attestation that the test was checked against an independent oracle, records source/log hashes, and permits L2 promotion only on a passing run with an exact runtime-version match. This runner is a manual verification path, not automatic lesson-pipeline execution.
- [x] Evidence applicability and verification timestamps are persisted; re-verification automation remains pending.
- [x] Review-result cache is keyed by claim/source/model/prompt/scope and has expiry.
- [ ] A dedicated factual-review worker is registered in the task runtime; retry/resource budgets and cost controls are enforced.
- [ ] Golden-set and calibration evaluation are implemented and repeatable.
- [ ] Existing database upgrade and backup/restore tests pass without data loss.
