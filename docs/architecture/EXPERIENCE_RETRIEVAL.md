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
