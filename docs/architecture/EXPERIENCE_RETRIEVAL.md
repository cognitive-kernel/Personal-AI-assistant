# Hybrid retrieval for learning experiences

## Data and trust boundary

Only records whose current status is `verified` are candidates for reusable guidance. Failed and `needs_review` records are retrieved through the separate warning path and are never promoted by similarity scores. Legacy chat-loop records without successful semantic claim review are excluded from verified retrieval.

The canonical experience and its immutable version snapshots remain authoritative. Embeddings are derived data stored in `learning_experience_embeddings`, keyed by experience ID, experience version, and model name. A changed experience version cannot reuse an embedding generated for an older version. The cache may be deleted and rebuilt without losing the source record.

## Ranking

1. BM25-style lexical scoring matches exact technical terms, identifiers, and multilingual query variants.
2. Local Ollama embeddings provide semantic similarity when the configured embedding model is installed and reachable.
3. Reciprocal Rank Fusion combines the lexical and vector ranks without comparing scores from different scoring scales.
4. Only the top bounded candidate set is considered; vector similarity has a minimum threshold, and result count is capped.

The first implementation scans a bounded corpus and computes cosine similarity in Python. It is not an approximate-nearest-neighbor index and should be benchmarked before increasing the corpus limit. A future vector-store adapter can replace the scan without changing the experience record contract.

## Failure behavior and privacy

- Embeddings are generated only through the configured loopback-only Ollama client; no text is sent to an external embedding API.
- Input size, batch count, vector dimensions, numeric types, and finite values are validated before cache writes.
- If the embedding model is missing, times out, or returns invalid data, the query falls back to lexical ranking. It does not fail the chat/learning task solely because vector retrieval is unavailable.
- The model name and experience version are part of the cache key; switching models causes cache rebuild rather than mixing incompatible vectors.
- This cache is a retrieval aid, not evidence that an experience is true. Evidence and passing test results remain mandatory for verified status.


## Reproducible quality evaluation

A synthetic bilingual fixture lives at `tests/fixtures/experience_retrieval_eval.json`. Version 2 contains 13 synthetic experience records and 16 English/Persian queries, including cross-language variants, lease-recovery near-neighbors, backup-versus-migration distinctions, proxy-isolation cases, response-size limits, and queries with multiple relevant records. Relevance labels are fixture authoring judgments, not human-evaluated production data. The benchmark runner seeds an isolated temporary SQLite database, never the user's live database, and uses the configured local embedding model when available:

```bash
# Real local model, if the configured embedding model is installed:
python scripts/evaluate_experience_retrieval.py

# Deterministic baseline without a local model:
python scripts/evaluate_experience_retrieval.py --lexical-only
```

CI runs the lexical-only baseline and prints its report, including Recall@5, Precision@5, MRR, and per-query top results. The local-model command includes Recall@5, MRR, per-query ranking, embedding cache row count, and whether vector retrieval was available or lexical fallback was used. Recall@5 measures the fraction of relevant records found in the top five; Precision@5 is the number of unique relevant records in the top five divided by five; MRR is the mean reciprocal rank of the first relevant result. The fixture is synthetic and is only a regression benchmark, not evidence of general real-world retrieval quality. No measured benchmark result should be claimed until the runner has actually been executed against a configured local model.


The hard-case fixture is intended to expose ranking regressions and ambiguity rather than maximize a headline score. Always inspect per-query rankings and false positives alongside Recall@5/MRR. A perfect score on synthetic data still does not validate retrieval quality on real user experiences; real-data evaluation requires a privacy-reviewed, manually adjudicated dataset.


## Recorded synthetic lexical baseline

The CI run on 2026-10-16 for fixture v2 reported:

- Recall@5: 1.000
- Precision@5: 0.225
- MRR: 1.000

The regression test currently requires Recall@5 >= 0.90, Precision@5 >= 0.20, and MRR >= 0.90 on this exact synthetic fixture. These are change-detection thresholds only; they are not service-level objectives and must not be presented as real-world quality targets. A fixture change must include a reviewed explanation of relevance labels and a deliberate baseline update. The hybrid/vector mode still has no measured CI baseline because a local embedding model is not available in the standard CI runner.
