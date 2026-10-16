# Research-aware chat loop

## Routing

Ordinary small talk remains on the direct local Ollama chat path. Technical, troubleshooting, and explicit research requests use the bounded research path:

1. Retrieve up to three public HTTPS sources through `web_sources.discover_sources`, which applies destination validation, redirect restrictions, timeouts, and response-size limits.
2. Retrieve a small number of verified experience records and separate failed/review-needed warnings. Experience similarity is guidance, not factual evidence.
3. Ask the local model to answer in the user's language, treat source excerpts and memory as untrusted data, and cite exact source URLs.
4. Run claim-level review against the supplied source excerpts. If review identifies unsupported/uncited claims, allow one repair attempt and review again.
5. If no sources are available or review remains inconclusive, return the answer with a clear uncertainty note rather than claiming verification.

## Resource and safety bounds

- Source count: at most 3.
- Source excerpt size: at most 2,500 characters per source.
- Answer generation: at most one repair pass.
- Chat prompt content remains bounded by the Ollama client request limit.
- No generated code or shell command is executed.
- Only the local loopback Ollama endpoint is used for generation and embedding.

## Limitations

- A model-assisted claim review is not independent fact verification.
- Chat source excerpts and review outcomes are not yet persisted as a durable per-answer audit record.
- Retrieval evaluation using a curated Persian/English dataset with Recall@5 and MRR is still required.
- If external source discovery is unavailable, the chat answer is based on local model knowledge and is explicitly marked unverified.
