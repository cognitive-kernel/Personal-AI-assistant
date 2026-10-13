# AI runtime resource limits

The Ollama request context is capped according to detected total RAM:

- Unknown or less than 12 GiB: at most 2,048 tokens.
- 12 GiB to less than 24 GiB: at most 4,096 tokens.
- 24 GiB or more: at most 8,192 tokens.

If RAM telemetry is unavailable (for example, when the optional psutil import cannot be used), the runtime chooses the conservative 2,048-token cap rather than assuming a high-memory machine. An explicit lower `ollama_num_ctx` setting remains respected. CPU threads are independently capped by the detected logical CPU count.

These are resource-safety defaults, not a benchmark of answer quality or a guarantee that a particular model fits in memory. Validate model-specific memory use on the target host before increasing the configured context.
