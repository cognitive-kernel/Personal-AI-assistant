# Bilingual answer-quality benchmark

- **Dataset version:** `v1`
- **Cases:** `benchmarks/answer_quality/v1/cases.jsonl`
- **Languages:** Persian (`fa`) and English (`en`)
- **Current scope:** deterministic dataset/schema validation only; this is not a model-quality score.

Each JSONL case defines a prompt, expected concepts, an evidence policy, and claims the assistant must not make. The set targets evidence sufficiency, source conflicts, feedback isolation, and cross-language retrieval/source attribution.

## Validate

Run `pytest tests/test_answer_quality_dataset.py` to validate the dataset contract. Missing, empty, malformed, duplicate-ID, or one-language-only data must fail visibly.

## Evaluation protocol still required

A model-quality run must pin the model identifier/version, prompt/template version, retrieval corpus/index snapshot, runtime settings, and evaluation date. Score each response against expected concepts, prohibited claims, requested-language adherence, and source attribution; report per-language results and the aggregate, with adjudication for ambiguous cases. Do not report a quality score until that runner and a reproducible model run exist. Do not treat dataset validation as answer-quality validation.


## Local diagnostic runner

Run from the repository root on a machine where the configured loopback-only Ollama service is running:

```bash
python scripts/run_answer_quality_eval.py --output reports/answer-quality-v1.json
```

For a report without raw model answers:

```bash
python scripts/run_answer_quality_eval.py --output reports/answer-quality-v1-redacted.json --redact-answers
```

The runner uses an isolated temporary SQLite database so it does not add evaluation runs or memory records to the normal application databases. It executes the normal research pipeline, records the dataset hash, code commit when available, hashes of the research/review prompt modules, configured model name and local inventory metadata, plus per-case source URL/title/excerpt hashes from the privacy-preserving audit table. Live web retrieval is not fully replayable, and Ollama's tags endpoint does not provide a cryptographic model digest; the report explicitly records these limits.

The output includes raw answers by default to support human adjudication; treat the report as potentially sensitive and do not publish it without review. `--redact-answers` omits answer text. Per-case generation failures are recorded and make the run `incomplete`.

The automatic checks are deliberately **surface diagnostics**, not semantic scoring: they count exact normalized phrase matches for expected concepts and prohibited claims, check for a minimal language-script marker, and record whether source URLs were present. Paraphrases can be missed; matching phrases do not prove factual correctness. Human review is required before making model-quality claims.
