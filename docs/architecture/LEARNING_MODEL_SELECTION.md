# Learning model selection policy

## Scope

The learning cost manager is an advisory selector only when no hard constraint is configured. When an operator configures a task budget or minimum model-quality threshold, the lesson-generation path enforces that constraint before the first Ollama generation request.

## Selection contract

1. Read the configured per-model cost/quality profiles and enumerate locally installed Ollama models.
2. Consider only installed models with valid explicit cost rates and a configured quality score at or above the minimum threshold.
3. Estimate the generation-phase cost across the initial generation and one permitted repair request. The prompt-token estimate is deliberately conservative and tokenizer-independent; the completion allowance is the existing 1,200 + 1,400 token request ceiling.
4. Select the lowest estimated-cost eligible model. Ties prefer the higher configured quality score, then a stable model-name order.
5. Use the same selected model for initial generation and its repair so the two-call budget remains coherent.

## Fail-closed behavior

- If no profiles or constraints are configured, preserve the existing `learning_ollama_model` behavior and record that estimated cost is unknown. Avoid an unnecessary model-list request.
- If a budget or minimum quality threshold is configured but no eligible installed model exists, fail the learning job before generation.
- If installed-model discovery fails while a hard constraint is configured, fail before generation; do not bypass the constraint with the fallback model.
- If discovery fails without hard constraints, preserve the configured model and record the fallback reason and unknown cost.
- Invalid budget, threshold, or profile container types are rejected rather than silently normalized.

## Provenance and limitations

Every generated lesson records the selected model, selection status/reason, candidate count, estimated cost, estimated prompt/completion token counts, estimate method, and generation-phase budget scope in `quality.generation_provenance.model_selection`. Actual Ollama token counters remain in provider usage fields.

Scope limitation: this budget currently covers lesson generation and its optional single repair only. A separate model-assisted claim-review request, when triggered for web-retrieved sources, is not included in this estimate and remains a follow-up integration item. Configured quality scores are operator-supplied estimates, not live benchmark results. Cost estimates require explicit rates and are not invoices. The character-based prompt estimate is conservative but cannot exactly reproduce model-specific tokenization. Live Persian/English quality evaluation, independent adjudication, and target-host verification remain separate acceptance gates.
