# ADR-020 — Adaptive Codex context bounded by model and hardware capacity

- **Status:** Accepted
- **Date:** 2026-11-12
- **Decision owners:** Repository owner / Autonomous Engineering maintainers

## Context

The previous Codex/Ollama integration treated 64,000 tokens as a mandatory minimum in the Python selector, settings readiness API, PowerShell preflight, runner checks, and Codex model catalog. This prevented execution when either the selected model or the available host memory could safely support a smaller context, even though both constraints can be handled by reducing the requested context.

The model's advertised context limit and the host's memory-derived context capacity are independent constraints. A model may support a large context while the host cannot allocate it; conversely, the host may have sufficient memory while the model has a smaller context limit. Neither value may override the other.

## Decision

Codex must adapt its configured context to the actual safe capacity. There is no Codex-specific 64k minimum.

Let:

- C_model be the selected model's architecture-specific context_length.
- M_available be currently available physical memory before loading the derived Codex model.
- M_reserve be the configured system safety reserve.
- M_weights be the installed model's reported weight size.
- M_overhead be the conservative model/runtime overhead allowance.
- B_KV be the estimated KV-cache bytes per token from the model's architecture metadata.
- C_catalog be the Codex catalog ceiling (currently 1,048,576 tokens).

The host capacity is estimated independently:

\[
C_{hardware} =
\left\lfloor
\frac{M_{available}-M_{reserve}-M_{weights}-M_{overhead}}{B_{KV}}
\right\rfloor
\]

The effective ceiling and selected value are:

\[
C_{effective}=\min(C_{hardware},C_{model},C_{catalog})
\]

\[
C_{selected}=\min(C_{requested},C_{effective})
\]

When no explicit context is requested, select C_effective. Any requested value above the effective ceiling is clamped; it is never persisted or passed to Codex. The dashboard, API, workflow, PowerShell bridge, derived Ollama model, and Codex model catalog must all use the same verified selected value.

The implementation's minimum valid model/runtime context remains 512 tokens. This is a metadata/runtime validity boundary, not a Codex 64k policy. If the model metadata or safe hardware estimate cannot support 512 tokens, execution cannot be safely initialized and must report the exact resource/metadata failure rather than inventing a fallback.

## Runtime enforcement

1. Read architecture-specific model metadata and tool-calling capability from Ollama using bounded /api/show requests (verbose=false).
2. Estimate host capacity from current free physical memory, model weight size, KV-cache dimensions, reserve, and overhead.
3. Select the minimum of requested, hardware, model, and catalog ceilings.
4. Create or reuse a separate context-specific Ollama model variant with exactly the selected num_ctx; never mutate the original model.
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
- A 10,000-token hardware ceiling produces a 10,000-token Codex catalog even when the request is 64,000 or higher.
- A model ceiling of 8,192 tokens produces an 8,192-token Codex catalog when hardware can support more.
- A request above either ceiling is clamped and never persisted above it.
- The hardware-derived ceiling is reported independently from the model ceiling.
- Context below the 512-token validity boundary, missing KV metadata, missing memory telemetry, or insufficient memory fails safely.
- Settings/readiness accept valid models below 64,000 tokens.
- The actual /api/ps allocation must exactly match the selected context before and after Codex execution.
