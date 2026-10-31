# Research-aware chat loop

## Routing

Ordinary small talk remains on the direct local Ollama chat path. Both paths check likely Persian/English response-language mismatches; the direct path permits one language-only repair and returns a fixed safe fallback if the mismatch persists. Technical, troubleshooting, and explicit research requests use the bounded research path:

1. Retrieve up to three public HTTPS sources through `web_sources.discover_sources`, which applies destination validation, redirect restrictions, timeouts, and response-size limits.
2. Retrieve a small number of verified experience records and separate failed/review-needed warnings. Experience similarity is guidance, not factual evidence.
3. Ask the local model to answer in the user's language, treat source excerpts and memory as untrusted data, and cite exact source URLs.
4. Run claim-level review against the supplied source excerpts. If review identifies unsupported/uncited claims, allow one repair attempt and review again.
5. If no sources are available or review remains inconclusive, return the answer with a clear uncertainty note rather than claiming verification.

## Resource and safety bounds

- Source count: at most 3.
- Source excerpt size: at most 2,500 characters per source.
- Answer generation: at most one repair pass for language mismatch; the research path may additionally use one bounded claim-support repair. The language guard runs again after claim repair, before the second claim review, because a repair generation can regress language consistency.
- Chat prompt content remains bounded by the Ollama client request limit.
- No generated code or shell command is executed.
- Only the local loopback Ollama endpoint is used for generation and embedding.
- Each persisted assistant message can receive one replaceable positive/negative user rating. Feedback is stored separately from knowledge, never changes verification status, and is removed when its conversation is deleted.

## Limitations

- A model-assisted claim review is not independent fact verification.
- A local audit row stores query/answer hashes, source URLs/titles/content hashes, review status and summary, memory record references, duration, and bounded claim-review provenance (`prompt_version`, prompt SHA-256, model ID, optional model digest/status, and numeric provider usage counters). Raw query text, answer text, source excerpts, claim text, and free-form review reasons are not stored in this audit table. A missing model digest remains explicitly marked unavailable; the audit does not infer an immutable model identity from a model name.
- Retrieval evaluation using a curated Persian/English dataset with Recall@5 and MRR is still required.
- If external source discovery is unavailable, the chat answer is based on local model knowledge and is explicitly marked unverified.
- Empty or non-text model responses fail closed with a localized fallback. The research path records a `generation_failed` audit outcome and skips claim review rather than treating an empty draft as a valid answer. The same validation applies to language-repair and claim-repair generations: invalid repair output is audited as a generation failure and is never passed to claim review, string concatenation, or experience capture.
