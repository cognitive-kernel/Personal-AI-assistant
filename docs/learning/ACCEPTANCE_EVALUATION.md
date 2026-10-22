# Learning acceptance evaluation

`scripts/evaluate_learning_acceptance.py` measures observed accept/reject decisions against a versioned, human-adjudicated reference dataset. It is an offline evaluation harness: it does not call a model, establish truth, or modify the learning database.

## Keep reference labels immutable

The reference dataset and each evaluation run are separate JSON files. Never put observed model decisions into the reference dataset; keeping observations separate allows the same adjudicated cases to be reused across model, prompt, and code versions.

The reference dataset has `schema_version: 1`, a non-empty `dataset_name`, `dataset_version`, and `cases`. Each case requires a unique stable `id`, `domain`, `language`, `expected_decision` (`accept` or `reject`), `source_refs`, and `adjudication` containing `reviewer`, ISO-8601 `adjudicated_at`, a non-empty `rationale`, and `independent_of_model: true`.

The observation file has `schema_version: 1`, `observations` keyed by the same case IDs, and `run_metadata` containing `model_id`, `model_digest`, `prompt_version`, `application_commit`, and ISO-8601 `evaluated_at`. Every reference case must have exactly one observation; unknown and missing IDs fail closed.

Do not add generated or synthetic cases and call them golden data. Commit reference cases only after their expected decisions and rationales have been independently reviewed. Keep secrets out of both files.

## Run

```sh
python scripts/evaluate_learning_acceptance.py path/to/reference-dataset.json path/to/run-observations.json
```

The JSON report includes the confusion matrix, precision, recall, F1, accuracy, false-acceptance rate, false-rejection rate, case-level outcomes, dataset version, and run metadata. A metric is `null` when its denominator is absent; it is not silently reported as zero.

To enforce explicit release thresholds:

```sh
python scripts/evaluate_learning_acceptance.py path/to/reference-dataset.json path/to/run-observations.json \
  --max-false-acceptance-rate 0.02 \
  --max-false-rejection-rate 0.15
```

The command exits with status 1 when a configured threshold is exceeded or cannot be computed, and status 2 for invalid input. Thresholds above are examples, not approved project policy; release owners must choose them based on risk and the adjudicated set.

## Limitations and next steps

- This harness reports results; it does not generate trusted labels or prove general model quality.
- No human-adjudicated learning acceptance dataset is bundled yet. The harness must not be described as a completed golden-set evaluation until such a dataset exists and has been reviewed.
- Add domain-specific cases for source support, unsupported claims, material source conflicts, prompt injection, and ambiguous/inconclusive evidence. Evaluate Persian and English separately.
- Record the exact source hashes used for adjudication and retain run reports as CI artifacts. A passing result on a small dataset is not sufficient evidence of broad reliability.
- Learning-job quality metadata now records the prompt contract version and SHA-256 fingerprints for the exact generation/repair prompt payloads, plus attempt count and model ID. The current Ollama path does not provide a trusted model digest, so that field remains explicitly unavailable; do not treat prompt fingerprints as a substitute for a model digest or a human-adjudicated reference set.
