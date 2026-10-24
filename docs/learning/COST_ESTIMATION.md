# Learning token cost estimates

The learning pipeline records Ollama-reported prompt/output token counts and timings where available. These usage counters do not themselves establish monetary cost.

## Optional exact-model USD estimates

Set `LEARNING_MODEL_PRICING_JSON` in the process environment only when a reviewed rate card is available. The JSON object must key each entry by the exact model identifier recorded in learning provenance:

```json
{
  "exact-model-id:exact-tag": {
    "input_usd_per_million_tokens": "0.10",
    "output_usd_per_million_tokens": "0.20",
    "pricing_version": "vendor-rate-card-2026-10-24"
  }
}
```

Rates are decimal strings in USD per one million tokens. `pricing_version` is mandatory so the rate card used for an estimate is traceable. Do not use a broad alias or `latest` unless that is the exact model identifier and the price card explicitly applies to it.

The estimator returns `unknown` when no exact model entry or valid rate card exists, and `partial` when any stage lacks either prompt or output token counts. It never fills missing counters with zero. A total is emitted only when usage is complete for every recorded stage. The result is an estimate, not a provider invoice; local Ollama execution can still incur infrastructure/electricity costs not represented by token rates. Peer-provider costs remain unknown unless their usage is separately captured and routed through an applicable rate card.

Do not use estimates as a promotion signal or as proof that a provider charged that amount. Review rate cards whenever model versions, provider pricing, or billing rules change.
