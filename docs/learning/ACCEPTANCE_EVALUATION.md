# Learning acceptance evaluation

`scripts/evaluate_learning_acceptance.py` measures observed accept/reject decisions against a versioned, human-adjudicated dataset. It is an offline evaluation harness: it does not call a model, establish truth, or modify the learning database.

## Dataset contract

Use a JSON object with `schema_version: 1`, a non-empty `dataset_version`, and non-empty `cases`. Each case requires:

- a unique stable `id`, `domain`, and `language`;
- `expected_decision` (`accept` or `reject`) assigned by an independent human reviewer;
- `observed_decision` (`accept` or `reject`) emitted by the system run being evaluated;
- `source_refs`, containing the exact source references used during adjudication;
- `adjudication.reviewer`, `adjudication.adjudicated_at`, `adjudication.rationale`, and `adjudication.independent_of_model: true`.

Do not add generated or synthetic cases and call them golden data. A dataset should be committed only after its expected decisions and rationales have been independently reviewed. Keep the dataset version and the model/prompt/build metadata for each evaluation run.

## Run

```sh
python scripts/evaluate_learning_acceptance.py path/to/adjudicated-learning-set.json
```

The JSON report includes the confusion matrix, precision, recall, F1, accuracy, false-acceptance rate, and false-rejection rate. A metric is `null` when its denominator is absent; it is not silently reported as zero.

To enforce explicit release thresholds:

```sh
python scripts/evaluate_learning_acceptance.py path/to/adjudicated-learning-set.json \
  --max-false-acceptance-rate 0.02 \
  --max-false-rejection-rate 0.15
```

The command exits with status 1 when a configured threshold is exceeded or cannot be computed, and status 2 for invalid dataset input. Thresholds above are examples, not approved project policy; release owners must choose them based on risk and the adjudicated set.

## Limitations and next steps

- This harness reports results; it does not generate trusted labels or prove general model quality.
- No human-adjudicated learning acceptance dataset is bundled yet. The harness must not be described as a completed golden-set evaluation until such a dataset exists and has been reviewed.
- Add domain-specific cases for source support, unsupported claims, material source conflicts, prompt injection, and ambiguous/inconclusive evidence. Evaluate Persian and English separately.
- Record model digest, prompt versions, application commit, source hashes, and evaluation timestamp alongside each report.
