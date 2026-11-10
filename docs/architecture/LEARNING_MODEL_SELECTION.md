# Learning model selection policy

## Scope

The learning engine is intentionally pinned to the configured `learning_ollama_model`. For this project the default is `gpt-oss:20b`; chat and programming model settings remain independent. Cost/quality profiles are an eligibility gate for this one model, not permission to silently switch to a different installed model.

## Selection contract

1. Read the configured learning model and enumerate installed Ollama models whenever cost profiles or hard constraints require profile selection.
2. If the configured model is not installed, fail before lesson generation. Startup bootstrap installs and verifies the dedicated learning model; direct application startup reports the missing-model failure rather than choosing a substitute.
3. When cost profiles are configured, consider only the configured learning model. Its profile must contain explicit cost rates and a quality score at or above the configured threshold.
4. Enforce the configured job budget against the estimated maximum cost of initial generation, one permitted repair, and one optional claim-review request when usable web evidence exists.
5. Use the same configured model for initial generation, repair, optional claim review, and research-first curriculum outline generation. Record the actual model and usage separately from estimates.

## Fail-closed behavior

- Never substitute a cheaper or otherwise installed model for the configured learning model.
- If the configured model is missing, fail before generation with an actionable error.
- If a budget or minimum quality threshold is configured but the configured model has no eligible profile, fail before generation.
- If installed-model discovery fails while a hard constraint is configured, fail before generation; do not bypass the constraint.
- If no cost profiles or hard constraints are configured, preserve the configured model and record estimated cost as unknown; no model-list request is required by the cost selector.
- Invalid budget, threshold, or profile container types are rejected rather than silently normalized.

## Provenance and limitations

Every generated lesson records the selected model, selection status/reason, candidate count, estimated cost, estimated prompt/completion token counts, estimate method, and generation-phase budget scope in `quality.generation_provenance.model_selection`. Actual Ollama token counters remain in provider usage fields.

Scope: the monetary estimate includes generation, one repair, and optional claim review when usable web sources exist. The separate durable request/token ledger reserves claim review as its own idempotent stage. Provider-reported usage remains authoritative for observed token counts; the monetary estimate is not a provider invoice or hard billing cap. Configured quality scores are operator-supplied estimates, not live benchmark results. The character-based prompt estimate is conservative but cannot exactly reproduce model-specific tokenization. Live Persian/English quality evaluation, independent adjudication, and target-host verification remain separate acceptance gates.
