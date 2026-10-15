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
