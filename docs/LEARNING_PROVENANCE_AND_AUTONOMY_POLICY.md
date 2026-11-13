# Learning provenance and autonomous acceptance policy

**Status:** Approved policy contract; implementation is staged behind explicit validation and must not be treated as enabled until persistence, API integration, promotion gates, and CI acceptance are complete.

## Required provenance

Every learning item must retain one or more structured source records. Extra source records are allowed; required fields may not be omitted.

| Source type | Mandatory metadata |
|---|---|
| Web page / official documentation | Direct HTTPS URL to the exact page used |
| AI-generated learning | Provider, model identifier, and a direct HTTPS URL to the reference source cited by that AI |
| Book | Exact book title and author name |
| Other | A meaningful description; URL and additional metadata where available |

A generated or repeated URL is not proof that the page supports the claim. The page must be safely retrieved, its relevant content bound to the claim, and its content hash and retrieval time retained. A failed/unavailable fetch is inconclusive, not a negative fact. A missing mandatory source record blocks trusted promotion but never deletes the candidate or its evidence.

For AI-originated material, the AI provider/model and the cited reference are separate facts. The reference URL must not be inferred from the model's name or from a general search result. If the AI cannot provide a reference, the item remains unverified until an acceptable reference is supplied and checked.

For books, title and author are mandatory. Edition, publisher, ISBN, chapter and page should be captured when available. A book record is not considered a web URL and must not be silently coerced into one.

## Four learning paths

The aggregate policy includes all four paths:
1. AI learning from AI;
2. Basic learning;
3. Curriculum/topic learning;
4. Experience system.

The experience system records the input/goal, origin path, source provenance, actions, outcome, errors, likely causes, corrections and final evaluation. Failed experiences remain retained as auditable evidence and are never silently discarded.

## Human-review reduction rule

Routine learning-content acceptance may become autonomous only when all of the following hold:

- Accuracy across the pooled, independently evaluated records from all four paths is **strictly greater than 95%**. Exactly 95% does not qualify.
- Every path is represented in the evaluation set.
- Every evaluation record has a unique sample ID, a supported verification method, a verifier identity, a timestamp with timezone, and a traceable evidence reference.
- A model cannot verify its own generated output. Model-based verification must identify a different verifier from the generator.
- Missing evidence, ambiguous outcomes, unavailable sources and untested claims do not count as correct.
- The aggregate gate is recalculated after new evaluations and regressions; a failed gate returns routine learning content to the review-required state.

The initial implementation deliberately does not invent a minimum sample count. The threshold is a mathematical accuracy ratio over independently verified records, not the model's self-reported confidence. A separate minimum sample/uncertainty policy must be approved before enabling unattended acceptance in production.

This rule concerns routine learning-content review only. It does not waive safety, authorization, security, privacy, legal, destructive-operation, or production-change controls. Those actions retain their own risk gates.

## Preservation and rollback

- Raw observations, failed experiences, contradictions, source snapshots, hashes, and evaluation records are append-only evidence.
- A new trusted knowledge version is additive and versioned. It does not overwrite or delete the previous knowledge store.
- Promotion requires the source contract, claim-level evidence, relevant tests, and current aggregate gate.
- If the gate falls to 95% or below, autonomous acceptance is disabled; existing records remain intact and can be re-evaluated.
- A failed module or unavailable verifier must not block unrelated learning paths; the affected item remains pending/inconclusive with a diagnostic reason.

## Implementation status

The implementation branch now contains:
- schema migrations for structured candidate-source records and immutable accuracy-evaluation records;
- automatic web-page and AI provider/model provenance capture for web-grounded lessons;
- a loopback-only source-metadata API and source details in candidate inspection;
- promotion checks that reject candidates without the mandatory AI reference and identified origin source;
- the aggregate accuracy evaluator wired into candidate readiness, promotion, and the autonomous test-plan path.

The gate remains **disabled by default** because trusted verifier adapters have not yet been connected to all four learning paths. Until all four paths contribute independently evidenced evaluation records and the aggregate ratio exceeds 95%, the existing review gate remains active. This branch must pass CI on its exact head before merge; passing CI alone does not prove live-provider or target-host acceptance.
