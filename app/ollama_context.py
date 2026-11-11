from __future__ import annotations

import json
import sys
from typing import Any

MIN_CONTEXT_TOKENS = 512
MIN_CODEX_CONTEXT_TOKENS = 64000
MAX_CODEX_CONTEXT_TOKENS = 1048576
GIB = 1024 ** 3
DEFAULT_RAM_RESERVE_BYTES = 4 * GIB
MIN_MODEL_OVERHEAD_BYTES = GIB // 2
MODEL_OVERHEAD_RATIO = 0.10


class OllamaModelMetadataError(ValueError):
    """Raised when model or host metadata is insufficient for a safe context choice."""


def maximum_context_tokens(model_info: dict[str, Any], architecture: str) -> int:
    if not isinstance(model_info, dict) or not isinstance(architecture, str) or not architecture.strip():
        raise OllamaModelMetadataError("Model architecture/context metadata is unavailable.")
    value = model_info.get(f"{architecture}.context_length")
    if isinstance(value, bool) or not isinstance(value, int) or value < MIN_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"Model metadata does not expose a valid {architecture}.context_length."
        )
    return value


def supports_tool_calling(capabilities: Any) -> bool:
    return isinstance(capabilities, list) and "tools" in capabilities


def select_context_window(
    *,
    model_info: dict[str, Any],
    architecture: str,
    model_weight_bytes: int,
    available_memory_bytes: int,
    requested_context_tokens: int | None = None,
    reserve_bytes: int = DEFAULT_RAM_RESERVE_BYTES,
) -> dict[str, int | None]:
    """Select min(requested, model maximum, measured safe RAM capacity).

    The RAM estimate assumes float16 KV cache, reserves 4 GiB for Windows and
    other processes, and adds at least 0.5 GiB or 10% of model weights as overhead.
    Missing telemetry or architecture dimensions are errors, never fallback values.
    """
    maximum = maximum_context_tokens(model_info, architecture)
    if maximum < MIN_CODEX_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"Selected model maximum context {maximum} is below the Codex minimum {MIN_CODEX_CONTEXT_TOKENS} tokens."
        )
    numeric_inputs = {
        "model_weight_bytes": model_weight_bytes,
        "available_memory_bytes": available_memory_bytes,
        "reserve_bytes": reserve_bytes,
    }
    for name, value in numeric_inputs.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OllamaModelMetadataError(f"{name} must be a positive integer.")

    if requested_context_tokens is not None and (
        isinstance(requested_context_tokens, bool)
        or not isinstance(requested_context_tokens, int)
        or requested_context_tokens < MIN_CODEX_CONTEXT_TOKENS
    ):
        raise OllamaModelMetadataError(
            f"Requested Codex context must be an integer of at least {MIN_CODEX_CONTEXT_TOKENS} tokens."
        )

    prefix = f"{architecture}."
    required = {
        "layers": f"{prefix}block_count",
        "kv_heads": f"{prefix}attention.head_count_kv",
        "key_length": f"{prefix}attention.key_length",
        "value_length": f"{prefix}attention.value_length",
    }
    dimensions: dict[str, int] = {}
    for name, key in required.items():
        value = model_info.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OllamaModelMetadataError(f"Required KV-cache metadata '{key}' is unavailable.")
        dimensions[name] = value

    bytes_per_token = (
        dimensions["layers"]
        * dimensions["kv_heads"]
        * (dimensions["key_length"] + dimensions["value_length"])
        * 2
    )
    if bytes_per_token <= 0:
        raise OllamaModelMetadataError("KV-cache memory estimate is invalid.")

    overhead_bytes = max(
        MIN_MODEL_OVERHEAD_BYTES,
        int(model_weight_bytes * MODEL_OVERHEAD_RATIO),
    )
    cache_budget_bytes = (
        available_memory_bytes
        - reserve_bytes
        - model_weight_bytes
        - overhead_bytes
    )
    if cache_budget_bytes <= 0:
        raise OllamaModelMetadataError(
            "Available RAM cannot hold the model weights and required safety reserve."
        )

    hardware_maximum = min(maximum, cache_budget_bytes // bytes_per_token)
    if hardware_maximum < MIN_CODEX_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"Available RAM cannot safely support the Codex minimum {MIN_CODEX_CONTEXT_TOKENS}-token context."
        )

    requested = requested_context_tokens
    selected = min(
        requested if requested is not None else maximum,
        maximum,
        hardware_maximum,
        MAX_CODEX_CONTEXT_TOKENS,
    )
    if selected < MIN_CODEX_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"No safe Codex context window of at least {MIN_CODEX_CONTEXT_TOKENS} tokens could be selected."
        )

    return {
        "maximum_context_tokens": maximum,
        "hardware_maximum_context_tokens": hardware_maximum,
        "codex_maximum_context_tokens": MAX_CODEX_CONTEXT_TOKENS,
        "requested_context_tokens": requested,
        "selected_context_tokens": selected,
        "kv_cache_bytes_per_token": bytes_per_token,
        "model_weight_bytes": model_weight_bytes,
        "available_memory_bytes": available_memory_bytes,
        "reserve_bytes": reserve_bytes,
        "overhead_bytes": overhead_bytes,
    }


def _main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise OllamaModelMetadataError("Context selection input must be a JSON object.")
        result = select_context_window(
            model_info=payload.get("model_info"),
            architecture=payload.get("architecture"),
            model_weight_bytes=payload.get("model_weight_bytes"),
            available_memory_bytes=payload.get("available_memory_bytes"),
            requested_context_tokens=payload.get("requested_context_tokens"),
        )
        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OllamaModelMetadataError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
