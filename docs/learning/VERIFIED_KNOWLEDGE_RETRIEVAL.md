# Verified factual-knowledge retrieval gate

Current implementation lives in `app.factual_knowledge.py` and `app.chat_research.py`.

## Current behavior

- `list_retrievable_claims()` is the policy gate; `search_retrievable_claims()` adds bounded lexical relevance for a query.
- Retrieval is wired into the technical/research path used by `POST /api/chat`; small-talk remains on the direct local-chat path.
- The query must resolve to one unambiguous, explicitly supported technical domain and environment. Ambiguous or unrecognized topics fail closed to an empty factual-knowledge context.
- Only claims with lifecycle `active`, evidence level `L2_tested` or `L3_human_verified`, unexpired re-verification state, traceable HTTPS source evidence with a content hash, and at least one passing test matching the claim's environment name/version are eligible.
- Results are bounded and include claim identity, source URL/hash/excerpt, and passing-test metadata. The current chat context caps the result at three claims and limits source/test details per claim.
- Factual test promotion requires a persisted local review audit binding the exact claim revision and test-source SHA-256 to an HTTPS oracle URL, a SHA-256 digest of the reviewed reference excerpt, and a review note. A task without a matching audit fails before sandbox execution. This is traceability, not cryptographic proof that the human review was independent or correct.
- The allowlist is defined by the domain resolver in `app/learning.py`; support for a domain does not imply that all claims in that domain are eligible.
- Retrieval failures return no factual-knowledge context and are logged without exposing database details or blocking the ordinary answer path.

## Security and trust boundaries

Retrieved claims and excerpts are data, not instructions. The research system prompt and per-request instruction explicitly tell the model not to follow instructions embedded in sources or memory. Source excerpts can still contain adversarial text, so this is a defense-in-depth prompt boundary, not a guarantee that a model cannot be influenced by prompt injection.

A retrieved claim is not proof of truth. Eligibility means it met the repository's current provenance, lifecycle, scope, and test gates. Model-assisted answer review is not an independent human adjudication and does not upgrade a stored claim's trust level.

## Regression coverage

- `tests/test_factual_knowledge_retrieval.py` covers lifecycle/evidence eligibility, expiry, source provenance, matching passing tests, and bounded result limits.
- `tests/test_chat_research.py` covers domain-scoped retrieval into chat context and a regression case where an evidence excerpt contains an instruction-injection string; the excerpt remains untrusted data and the system/request policy must explicitly reject following it.
- CI coverage does not replace a live-model prompt-injection evaluation or a human-adjudicated golden set.

## Remaining work

- Build a versioned, human-adjudicated golden set for relevance, citation adherence, prompt-injection resistance, and false acceptance.
- Validate that the supplied oracle excerpt hash actually corresponds to the cited URL/content, or capture and hash the excerpt server-side under an SSRF-safe fetch policy. The current audit records the supplied hash and URL but does not fetch or independently verify their correspondence.
- Measure Recall@k/MRR and answer citation correctness across Persian and English queries with a real embedding/model setup; do not infer quality from unit tests alone.
- Expand domain coverage only with exact environment resolution and tests that preserve the fail-closed retrieval gate.
- Consider sandbox execution only for explicitly allow-listed executable claims with current consent and an independent oracle. Never execute arbitrary source text or model-generated code as part of ordinary chat.
