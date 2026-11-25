# ADR-025: Run-scoped temporary Ollama context variants

- Status: Accepted for implementation; target-host acceptance pending
- Date: 2026-11-25
- Owners: Autonomous Engineering maintainers

## Context

The Codex context preflight calculates a safe context from the selected installed base model, model metadata, available physical RAM, model weight size, and KV-cache estimate. The previous implementation persisted a shared `<base>-codex-ctx` tag and only re-verified its allocation after the task. Separately, the legacy cleanup helper could delete numeric-suffix tags across every model family when their saved `num_ctx` matched the suffix. Those tags did not carry proof that they belonged to the current run. A separate settings migration also copied the legacy generic model value directly into a new chat profile; if that value was a temporary Codex variant, it could persist as a non-selectable chat model.

This did not satisfy the required lifecycle of creating a hardware-sized context variant for one engineering run and removing that variant after the run, while preserving user-owned models.

## Decision

1. Keep the operator-selected Ollama base model installed and unchanged. Do not pull, install, overwrite, or delete it as part of context setup or cleanup.
2. Calculate the context from verified model metadata and current host memory. Missing or invalid telemetry fails closed; no context is guessed.
3. Create a unique temporary tag using the base tag, selected context, GitHub run ID, run attempt, and a random nonce:
   `<base>-codex-ctx<context>-paa-<run-id>-<attempt>-<nonce>`.
4. Persist the exact temporary tag and context to the workflow environment before calling Ollama create, so an always-run cleanup step can recover after partial preflight failure.
5. After the task (including failure paths), cleanup may unload and delete only the exact tag whose run ID, attempt, nonce format, and saved `num_ctx` match the current workflow. The base tag must still exist. Cleanup verifies the temporary model is no longer loaded before deleting it and confirms the tag is absent afterward.
6. If ownership, context, base-tag presence, unload state, or deletion cannot be verified, preserve the model and fail cleanup. Never broaden cleanup to other tags.
7. Legacy numeric aliases without run-ownership metadata are audit-only by default. Destructive legacy cleanup requires the explicit `--delete-confirmed-legacy` option and is not part of startup or normal task preflight.
8. The same lifecycle applies to the autonomous engineering workflow and the Windows Codex sandbox diagnostic workflow.
9. When creating a missing chat profile from the legacy generic model setting, strip an internal Codex context suffix and seed the base model only. If an existing chat profile contains a reserved ephemeral Codex context tag, normalize only that tag to its base model because the temporary tag is deleted after its task. Preserve the legacy setting and all valid existing chat model choices; an explicit CHAT_OLLAMA_MODEL environment override remains authoritative.

## Consequences

- Each run has an isolated Ollama model configuration while reusing the base model's underlying weights; this does not download a second copy of the model weights.
- Concurrent or unrelated user model tags are not reused or deleted by the current run.
- A hard host shutdown or forced Runner termination can prevent the workflow's `always()` step from executing. A leftover run-scoped tag is safe to inspect and must not be removed by a broad startup sweep.
- Unit tests cover ownership mismatch, context mismatch, idempotency, unload verification, deletion verification, and base-model preservation. Exact-head CI and Windows target-host execution remain release gates.

## Rejected alternatives

- Reusing one stable `<base>-codex-ctx` tag: it is not scoped to a task and leaves the generated model configuration behind.
- Deleting every `-codex-ctxN` tag during the next preflight: legacy names alone do not prove current-run ownership and may refer to user-maintained models.
- Deleting a model while it remains loaded or when the base tag is missing: risks active use or removes the only remaining reference to model weights.
