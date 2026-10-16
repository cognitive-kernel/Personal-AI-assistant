# Claim-level evidence review

## Contract

`review_answer_claims(question, answer, sources)` asks the configured local Ollama model to assess material factual claims against a bounded set of source excerpts. It is a skeptical second pass, not independent proof. The output is reusable only when the response is valid JSON, every claim has an allowed verdict, every supported/contradicted claim cites a URL from the supplied evidence set, and all claims are supported and cited.

A URL citation does not prove a claim by itself. Evidence URLs must exactly match retrieved source URLs; answer-side citations are parsed as complete URL tokens, not substring matches. Trailing sentence punctuation, surrounding Markdown angle brackets, and quotation punctuation are tolerated, but URL suffixes, query strings, and lookalike hosts do not count as the same URL.

## Failure behavior

Malformed JSON, missing claims, unknown evidence URLs, invalid verdicts, or unsupported claims fail closed to `passed=false`. The result records `local_ollama_model_assisted_not_independent` so it cannot be mistaken for an independent review.

## Integration status

The learning-job path now calls the review primitive after deterministic lesson-quality checks when retrieved web source excerpts are available. It asks the generator to include exact source URLs, extracts at most three HTTPS source excerpts, stores the review result in the quality record and operational evidence log, and uses `passed_model_assisted` or `needs_review` as a separate review status. A review failure does not discard an otherwise usable lesson; the canonical knowledge item remains `unverified` either way.

This integration does not yet apply claim review to the normal chat endpoint. A passing review is still model-assisted and is not independent proof; factual verification and any later promotion policy must remain explicit.
