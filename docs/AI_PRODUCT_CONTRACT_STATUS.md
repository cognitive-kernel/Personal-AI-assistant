# AI Product-Contract Status Audit

**Audit date:** 2026-10-13  
**Baseline:** `main` at `f557a807f256bb2023b90174cf673ee5e27d71fd`  
**Scope:** Evidence-backed status of the requested personal-assistant product contract. This is a snapshot, not a claim that open PRs are integrated.

## Status vocabulary

- **IMPLEMENTED**: code and relevant tests are present on the inspected ref, with evidence.
- **PARTIAL**: a bounded slice exists, but one or more contract requirements remain.
- **MISSING**: no implementation/test evidence was found in the inspected refs.
- **BLOCKED**: a meaningful validation is not currently possible due to a stated dependency/environment constraint.
- **UNVERIFIED**: evidence is insufficient to assign a stronger status.

## Capability map

| Product requirement | Status | Evidence and limitation |
|---|---|---|
| Use an existing local LLM; do not train a foundation model | PARTIAL | The main README documents local Ollama and the default model. Live Ollama integration is not evidenced by the inspected CI. |
| Persian and English answers | PARTIAL | The learning loop PR documents language-aware checks and Persian/English query expansion. A versioned Persian/English answer-quality benchmark has not been run. See [PR #8](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/8). |
| Intent detection and orchestration | PARTIAL | PR #8 describes technical/research intent detection and bounded repair. Coverage across general user intents is not established by the evidence reviewed here. |
| Conversation context and persistent user feedback | PARTIAL | [PR #11](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/11) separates persisted feedback from the trusted system message and adds regression coverage. This does not prove resistance to all prompt injection. |
| Long-term experience memory with immutable versions and trust states | PARTIAL | PR #8 describes versioned experience snapshots and guarded status changes. Model-assisted review is not independent proof of truth. |
| Discover/maintain a topic curriculum from authoritative sources | PARTIAL | `app/curricula.py` and `app/curricula_expansion.py` exist on main, and the repository documents curriculum APIs. General automatic discovery of recognized curricula and verified beginner-to-advanced coverage is not demonstrated by this evidence. |
| Source ingestion, provenance and policy checks | PARTIAL | [PR #6](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/6) describes an allowlisted ingestion baseline and hashes/chunks. Comprehensive license/freshness review and source-policy coverage remain outstanding. |
| Hybrid retrieval / reranking | PARTIAL | PR #8 describes embeddings, cosine ranking, SQLite FTS5 and reciprocal-rank fusion with lexical fallback. The labeled relevant corpus has not been confirmed indexed, so no real Recall@5/MRR result is claimed. |
| Persian-to-English and English-to-Persian retrieval | UNVERIFIED | Query expansion is described in PR #8, but a dedicated cross-language retrieval test with indexed, labeled examples has not been evidenced in this audit. |
| Claim-level evidence review and exact citation URL checks | PARTIAL | PR #8 describes fail-closed model-assisted review; [PR #13](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/13) adds exact URL-token matching and regression tests. Neither establishes factual truth independently. |
| Web fallback when local evidence is insufficient | MISSING | No completed bounded web-research fallback with authoritative-source selection and provenance reconciliation was evidenced in the inspected files/PR summaries. |
| Test generated solutions only in a permitted sandbox | PARTIAL | PR #8 describes a restricted synthetic in-memory SQL sandbox. This is not a general code-agent sandbox and does not evidence live solution execution. |
| Versioned Persian/English answer-quality set | MISSING | No completed, versioned answer-quality run with reported metrics was evidenced. |
| Retrieval benchmark that fails visibly without indexed corpus | PARTIAL | PR #8 adds guards and evaluator tests. The real labeled corpus has not been confirmed indexed or benchmarked. |
| Live Ollama smoke/integration test | BLOCKED | No live Ollama service was contacted in the reviewed run evidence; this remains a required environment-dependent check. |

## Highest-value next validation

1. Run `scripts/evaluate_retrieval.py` only after confirming that every relevant fixture URL is indexed with chunks; preserve its fail-fast behavior if not.
2. Add a dedicated cross-language fixture pair (Persian query → English source, English query → Persian source) and run it against a prepared, versioned indexed corpus.
3. Add and run a versioned Persian/English answer-quality set, reporting exact dataset version and metrics.
4. Run curriculum discovery/ingestion end-to-end and verify source URL, hash, policy decision, freshness/licensing metadata, and coverage gaps.
5. Run the Ollama smoke/integration test only where a permitted local service is available.

## Coordination notes

- This audit is documentation-only and does not alter `docs/PROJECT_PROGRESS.md`; dashboard consolidation belongs to the coordinator.
- PRs #8, #11, and #13 are open/stacked according to the inspected metadata. PR #15 is stacked on PR #14; PR #16 adjusts CI triggers for stacked PRs. Do not merge based on this audit.
- No exact-head CI result is inferred from a different SHA. Current-head CI and independent review must be verified separately before any merge decision.
