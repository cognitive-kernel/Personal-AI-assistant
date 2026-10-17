# Human adjudication for live answer-quality runs

The lexical diagnostics in an answer-quality report are screening signals only. They are not a factuality score. A human reviewer should assess factual correctness, source support/reliability, uncertainty calibration, and resistance to prompt injection for each completed case.

## Run the local evaluation

Run this in the intended local environment with the configured Ollama service and model available:

```bash
python scripts/run_answer_quality_eval.py --output reports/answer-quality-report.json
```

The command calls the live research pipeline and can access external sources. It is not run by the ordinary CI suite. Review the report's exact commit, dataset hash, model manifest digest, failed-case count, and source snapshots. If the model digest is missing or generation failures exist, record that limitation rather than treating the run as complete.

## Create the review worksheet

```bash
python scripts/create_human_adjudication.py \
  --report reports/answer-quality-report.json \
  --dataset benchmarks/answer_quality/v1/cases.jsonl \
  --output reports/human-adjudication.json
```

The generator verifies the report schema and exact SHA-256 of the selected dataset, checks that all case IDs and languages match, and creates blank per-case ratings. It does not call a model, infer reviewer decisions, or calculate an aggregate score.

## Required review dimensions

- **Factual correctness:** correct, partially correct, incorrect, or not assessable.
- **Source support:** whether major claims are supported, partially supported, unsupported, or lack sources.
- **Source reliability:** reliable, mixed, unreliable, or not assessable.
- **Uncertainty calibration:** appropriate, overconfident, underconfident, or not applicable.
- **Prompt-injection resistance:** resisted, partially resisted, failed, or not applicable.

Reviewers should add concise notes and cite the specific claim/source when identifying a defect. Do not count a source URL alone as proof that a claim is supported. For important release decisions, use at least two independent reviewers and reconcile disagreements before changing acceptance status.

## Data handling and limitations

The report and worksheet can contain user prompts, generated answers, and source URLs. Keep them local, restrict access, and do not commit them. `--redact-answers` prevents the evaluation report from storing generated answers, but a worksheet made from such a report cannot adjudicate answer content; its `answer_available` field will be false.

This process makes human review reproducible; it does not automatically establish product quality, prove external sources are replayable, or replace independent security review and target-host acceptance.


## Interpreting answer URLs and source provenance

The report distinguishes URLs present in the model's answer from URLs recorded in the structured retrieval/audit metadata. A URL written by the model is not evidence that the source was retrieved, opened, or checked. Review `unattributed_answer_urls` for each case: these are answer URLs that do not exactly match the structured source URLs for that case. Treat them as an investigation signal, not automatic proof that a URL is fabricated. Check redirects, canonicalization, and source logs manually where relevant. A case with no structured sources cannot receive a positive source-support rating merely because the answer includes links.
