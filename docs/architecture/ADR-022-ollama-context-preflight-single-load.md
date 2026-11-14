# ADR-022: Single-load Ollama context preflight

- Status: Proposed
- Date: 2026-11-14
- Scope: Windows autonomous Codex Ollama context preflight and post-run verification

## Context

The context preflight performed multiple sequential operations against a local Ollama runtime. When /api/show omitted capabilities, the live tool-call probe used keep_alive=0, which intentionally unloaded a model that was not already resident. The same preflight then issued a separate /api/generate warmup, cold-loading the derived Codex model again. The workflow subsequently ran the verifier in -VerifyOnly mode, where the old implementation repeated both capability inference and warmup. The workflow step allowed 12 minutes, so model-load latency could dominate the entire preflight and the post-run check could trigger another unnecessary inference.

A second reliability issue was that model existence detection treated every /api/show failure as model absent. Timeouts, service failures, and other transport errors could therefore be misinterpreted as a missing model and followed by an unnecessary /api/create attempt.

## Decision

1. Split the PowerShell implementation into focused API, metadata/runtime, planning, and warmup units under scripts/ollama_context/; keep verify_ollama_context.ps1 as the orchestration entry point.
2. Time every Ollama HTTP operation and the hardware/context-planning phase. Emit bounded diagnostic lines with method/path or phase and elapsed milliseconds; do not log prompts, credentials, host identity, or model output.
3. Defer a live tool-call probe until the persistent derived model exists. If capability metadata is absent, run exactly one live probe with an explicit bounded keep_alive=10m; this probe also warms the model used by Codex.
4. If Ollama explicitly advertises tools, retain the metadata contract and perform one bounded warmup with num_predict=1. If it explicitly omits tools, fail closed.
5. Make -VerifyOnly read-only with respect to inference: verify the saved Modelfile context and current /api/ps allocation only. If the model is not actually resident or the allocation differs, fail closed rather than silently reloading it.
6. Preserve the default residency-neutral behavior of verify_ollama_tools.py for other callers. The new --keep-alive option is explicit and restricted to a small allowlist.
7. Treat /api/show 404 as not installed; propagate other failures instead of converting them into model-creation attempts.

## Consequences

- When live tool probing is required, its one model load also prepares the exact derived model for Codex; the subsequent duplicate warmup is skipped.
- Post-run verification no longer creates inference load or hides an unloaded runtime behind a new warmup.
- Diagnostics identify whether latency comes from metadata, model creation, tool probing, context planning, or model warmup.
- An unloaded model after the autonomous task is now a visible verification failure. This is intentional: the post-run gate verifies the actual runtime state and does not manufacture success by reloading the model.
- A slow first model load remains possible and is not masked by a shortened timeout. The existing workflow timeout and safety checks remain unchanged.

## Validation

- Unit tests cover explicit keep-alive retention, default residency-neutral behavior, and invalid keep-alive values.
- Architecture tests assert the verifier's module boundaries, phase timing, single-load flow, and inference-free -VerifyOnly behavior.
- GitHub Actions on the exact PR head must pass before the change is considered CI-validated.
- The live Windows Runner and target-host timing remain operational evidence gates; no success is claimed without those results.

## Risks and mitigations

- Ollama versions may omit capability metadata: the explicit live probe remains the fallback and is bounded to 45 seconds.
- The post-run check can fail if the model has been evicted before verification: this is a deliberate fail-closed signal, not a reason to trigger another inference.
- The context calculation, model variant naming, persistent num_ctx, exact allocation comparison, loopback-only endpoint, and non-admin runner boundary remain unchanged.
