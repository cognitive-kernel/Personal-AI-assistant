# Learning model selection policy

## Scope

The learning cost manager is an advisory selector only when no hard constraint is configured. When an operator configures a task budget or minimum model-quality threshold, the lesson-generation path enforces that constraint before the first Ollama generation request.

## Selection contract

1. Read the configured per-model cost/quality profiles and enumerate locally installed Ollama models.
2. Consider only installed models with valid explicit cost rates and a configured quality score at or above the minimum threshold.
3. Estimate the maximum applicable job cost across initial generation, one permitted repair, and one optional claim-review request when usable web evidence exists. The claim-review prompt estimate uses the same bounded evidence and payload builder as execution, with a maximum 10,000-character answer. Prompt estimates are deliberately conservative and tokenizer-independent; completion allowance is 1,200 + 1,400 tokens for generation/repair and 1,000 for claim review.
4. Select the lowest estimated-cost eligible model. Ties prefer the higher configured quality score, then a stable model-name order.
5. Use the same selected model for initial generation, repair, and any optional claim review so the estimated job budget and actual model selection remain coherent. Reserve the claim-review request in the durable per-job/global token ledger before calling Ollama.

## Fail-closed behavior

- If no profiles or constraints are configured, preserve the existing `learning_ollama_model` behavior and record that estimated cost is unknown. Avoid an unnecessary model-list request.
- If a budget or minimum quality threshold is configured but no eligible installed model exists, fail the learning job before generation.
- If installed-model discovery fails while a hard constraint is configured, fail before generation; do not bypass the constraint with the fallback model.
- If discovery fails without hard constraints, preserve the configured model and record the fallback reason and unknown cost.
- Invalid budget, threshold, or profile container types are rejected rather than silently normalized.

## Provenance and limitations

Every generated lesson records the selected model, selection status/reason, candidate count, estimated cost, estimated prompt/completion token counts, estimate method, and generation-phase budget scope in `quality.generation_provenance.model_selection`. Actual Ollama token counters remain in provider usage fields.

Scope: the monetary estimate includes generation, one repair, and optional claim review when usable web sources exist. The separate durable request/token ledger reserves claim review as its own idempotent stage. Provider-reported usage remains authoritative for observed token counts; the monetary estimate is not a provider invoice or hard billing cap. Configured quality scores are operator-supplied estimates, not live benchmark results. Cost estimates require explicit rates and are not invoices. The character-based prompt estimate is conservative but cannot exactly reproduce model-specific tokenization. Live Persian/English quality evaluation, independent adjudication, and target-host verification remain separate acceptance gates.
