# Verified factual-knowledge retrieval gate

`app.factual_knowledge.list_retrievable_claims()` provides a strict eligibility filter for future retrieval-augmented generation. It is not a semantic search implementation and is not yet wired into ordinary chat responses.

A claim is eligible only when all of the following hold:

- the claim belongs to the currently supported `python` domain;
- lifecycle status is exactly `active`;
- evidence level is `L2_tested` or `L3_human_verified`;
- re-verification has not expired when `reverify_after` is set;
- at least one traceable source with a non-empty reference and content hash exists;
- at least one passing test matches the claim's environment name and version.

Results include source provenance, scope, and applicable passing-test records. `L3_human_verified` results sort before `L2_tested`. The maximum result count is 500. Malformed persisted scope or applicability JSON fails closed with `KnowledgePolicyError` rather than returning partial provenance.

The helper deliberately excludes raw, merely sourced, failed, conflicting, superseded, inactive, expired, source-less, and untested claims. This filter is an eligibility gate, not a proof of truth, and its existence must not be reported as completed RAG integration. A later change must wire it into retrieval, preserve source citations in generated answers, test prompt-injection resistance, and ensure that conflicts or stale sources cannot enter context.
