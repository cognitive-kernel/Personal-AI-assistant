# ADR-020 — Adaptive Codex context bounded by model and hardware capacity

- **Status:** Accepted
- **Date:** 2026-11-12
- **Decision owners:** Repository owner / Autonomous Engineering maintainers

## Context

The previous Codex/Ollama integration treated 64,000 tokens as a mandatory minimum in the Python selector, settings readiness API, PowerShell preflight, runner checks, and Codex model catalog. This prevented execution when either the selected model or the available host memory could safely support a smaller context, even though both constraints can be handled by reducing the requested context.

The model's advertised context limit and the host's memory-derived context capacity are independent constraints. A model may support a large context while the host cannot allocate it; conversely, the host may have sufficient memory while the model has a smaller context limit. Neither value may override the other.

## Decision

Codex must adapt its configured context to the actual safe capacity. The application does not enforce a fixed 64k floor. **Compatibility qualification:** the official [Ollama Codex integration guide](https://docs.ollama.com/integrations/codex) recommends a context window of at least 64k tokens. Values below 64k are therefore below the upstream-documented recommendation and are best-effort compatibility, not an equivalent recommended configuration. They remain selectable only when the selected model and host support the value and the real Codex tool-call smoke test succeeds.

Let:

- C_model be the selected model's architecture-specific context_length.
- M_available be currently available physical memory at preflight time.
- M_reserve be the configured system safety reserve.
- M_weights be the installed model's reported weight size.
- M_weight_charge be zero when the selected base model is already resident (because free physical memory already excludes its weights), otherwise M_weights.
- M_overhead be the conservative model/runtime overhead allowance.
- B_KV be the estimated KV-cache bytes per token from the model's architecture metadata.
- C_catalog be the Codex-safe signed 64-bit ceiling `floor((2^63 - 1) / 9)`. Codex stores context fields as i64 and derives automatic compaction with `(context_window * 9) / 10`; the lower bound avoids overflow in that multiplication. The application does not add an arbitrary 1,048,576-token cap; actual model and host limits normally bind first.

The host capacity is estimated independently from the model's advertised context ceiling. KV-cache bytes/token necessarily depend on the selected model architecture, but the hardware memory budget is reported separately. If the base model is already loaded, its weight size is not charged twice because the available-memory measurement already reflects resident weights:


\[
C_{hardware} =
\left\lfloor
\frac{M_{available}-M_{reserve}-M_{weight\_charge}-M_{overhead}}{B_{KV}}
\right\rfloor
\]

The effective ceiling and selected value are:

\[
C_{effective}=\min(C_{hardware},C_{model},C_{catalog})
\]

\[
C_{selected}=\min(C_{requested},C_{effective})
\]

When no explicit context is requested, select C_effective. Any requested value above the effective ceiling is clamped; it is never persisted or passed to Codex. The dashboard, API, workflow, PowerShell bridge, derived Ollama model, and Codex model catalog must all use the same verified selected value. The generated catalog sets both `context_window` and `max_context_window` to that value, and the CLI passes an explicit `model_context_window` override. This prevents inherited Codex configuration from raising the active window above Ollama's verified allocation.

There is no fixed minimum context window. The only numeric validity rule is that a context length must be a positive integer; a zero-token or negative context is not executable. If the host cannot provide even one token after safety reserves, or the model metadata exposes no positive context limit, report the exact resource/metadata failure rather than inventing a fallback.

## Runtime enforcement

1. Read architecture-specific model context metadata, a positive integer model weight size from `/api/tags`, all KV-cache dimensions used by the memory estimate, and tool-calling capability from Ollama using bounded /api/show requests (verbose=false). The readiness API and settings-save path must reject a model whose KV-cache dimensions are incomplete; a valid context_length alone is insufficient.
2. Estimate host capacity from current free physical memory, model weight size, KV-cache dimensions, reserve, and overhead.
3. Select the minimum of requested, hardware, model, and catalog ceilings.
4. Remove stale current-format and legacy `-codex-64k` variants for the selected base model before measuring current free RAM, then create a separate context-specific variant with exactly the selected num_ctx; never mutate the original model.
5. Verify the saved variant setting through /api/show.
6. Load the variant and verify the actual allocated context_length through /api/ps before dispatching Codex.
7. Supply the exact selected value in Codex's model catalog and repeat runtime verification after execution.

Any mismatch, missing telemetry, invalid architecture dimensions, missing tools capability, or actual allocation different from the selected value blocks that dispatch. A user- or workflow-supplied value cannot bypass the ceiling.

## Consequences

- Models with valid context limits below 64,000 tokens remain eligible when they advertise the required tools capability.
- Hosts with safe memory for fewer than 64,000 tokens remain eligible; Codex receives the smaller verified context.
- A smaller context limits the amount of prior conversation and repository evidence available in one turn. The controller should rely on its existing durable checkpoints and bounded task workflow rather than increasing context beyond the safe ceiling.
- The FP16 KV-cache calculation is a preflight estimate, not proof of allocation. Ollama's loaded runtime report is the final allocation check.
- The selected context can change between runs as free physical memory changes. The value must be recalculated for each preflight.

## Regression requirements

Tests must prove:
- A 10,000-token hardware ceiling produces a 10,000-token Codex catalog even when the request is 64,000 or higher; a one-token ceiling remains a valid positive context if the runtime accepts it.
- A model ceiling of 8,192 tokens produces an 8,192-token Codex catalog when hardware can support more.
- A request above either ceiling is clamped and never persisted above it.
- The hardware-derived ceiling is reported independently from the model ceiling.
- Zero/negative context, missing KV metadata, missing memory telemetry, or insufficient memory to provide even one token fails safely.
- Settings/readiness accept valid models below 64,000 tokens.
- The actual /api/ps allocation must exactly match the selected context before and after Codex execution.
