from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Context is a positive token count; Codex has no fixed minimum window.
MIN_CONTEXT_TOKENS = 1
# Codex ModelInfo context_window/max_context_window fields are signed 64-bit integers.
# Use the schema's representable ceiling; do not impose an arbitrary 1M-token cap.
MAX_CODEX_CONTEXT_TOKENS = (1 << 63) - 1
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
    model_weights_already_loaded: bool = False,
) -> dict[str, int | bool | None]:
    """Choose a safe context by adapting to the model and host capacity.

    Hardware capacity is estimated independently from the model's advertised
    context limit. The effective ceiling is the minimum of hardware capacity,
    model capacity, and the Codex catalog limit. A requested value above that
    ceiling is clamped; it never causes an over-allocation.

    The RAM estimate assumes a float16 KV cache, reserves 4 GiB for Windows and
    other processes, and adds at least 0.5 GiB or 10% of model weights as
    runtime overhead. If the base model is already resident, its weights are
    not charged twice because free physical memory already excludes them.
    Missing telemetry or architecture dimensions are errors,
    never guessed fallback values. A successful estimate is still verified
    against Ollama's actual loaded context before Codex is dispatched.
    """
    maximum = maximum_context_tokens(model_info, architecture)
    numeric_inputs = {
        "model_weight_bytes": model_weight_bytes,
        "available_memory_bytes": available_memory_bytes,
        "reserve_bytes": reserve_bytes,
    }
    for name, value in numeric_inputs.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OllamaModelMetadataError(f"{name} must be a positive integer.")
    if not isinstance(model_weights_already_loaded, bool):
        raise OllamaModelMetadataError("model_weights_already_loaded must be a boolean.")

    if requested_context_tokens is not None and (
        isinstance(requested_context_tokens, bool)
        or not isinstance(requested_context_tokens, int)
        or requested_context_tokens < MIN_CONTEXT_TOKENS
    ):
        raise OllamaModelMetadataError(
            f"Requested context must be a positive integer."
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
    # Free physical memory already excludes resident weights. Do not charge the
    # same weights a second time when Ollama reports the base model is loaded.
    model_weight_charge_bytes = 0 if model_weights_already_loaded else model_weight_bytes
    cache_budget_bytes = (
        available_memory_bytes
        - reserve_bytes
        - model_weight_charge_bytes
        - overhead_bytes
    )
    if cache_budget_bytes <= 0:
        raise OllamaModelMetadataError(
            "Available RAM cannot hold the model weights and required safety reserve."
        )

    # Keep the host-derived capacity independent of the model's own context cap.
    hardware_maximum = cache_budget_bytes // bytes_per_token
    if hardware_maximum < MIN_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"Available RAM cannot safely support even one context token."
        )

    effective_maximum = min(
        maximum,
        hardware_maximum,
        MAX_CODEX_CONTEXT_TOKENS,
    )
    requested = requested_context_tokens
    selected = min(
        requested if requested is not None else effective_maximum,
        effective_maximum,
    )
    if selected < MIN_CONTEXT_TOKENS:
        raise OllamaModelMetadataError(
            f"No positive safe runtime context could be selected."
        )

    return {
        "maximum_context_tokens": maximum,
        "hardware_maximum_context_tokens": hardware_maximum,
        "effective_maximum_context_tokens": effective_maximum,
        "codex_maximum_context_tokens": MAX_CODEX_CONTEXT_TOKENS,
        "requested_context_tokens": requested,
        "selected_context_tokens": selected,
        "kv_cache_bytes_per_token": bytes_per_token,
        "model_weight_bytes": model_weight_bytes,
        "model_weight_charge_bytes": model_weight_charge_bytes,
        "model_weights_already_loaded": model_weights_already_loaded,
        "available_memory_bytes": available_memory_bytes,
        "reserve_bytes": reserve_bytes,
        "overhead_bytes": overhead_bytes,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a safe Ollama Codex context window.")
    parser.add_argument("--input-file", type=Path, help="Read JSON input from a file instead of stdin.")
    args = parser.parse_args(argv)
    try:
        if args.input_file is not None:
            payload = json.loads(args.input_file.read_text(encoding="utf-8"))
        else:
            payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise OllamaModelMetadataError("Context selection input must be a JSON object.")
        result = select_context_window(
            model_info=payload.get("model_info"),
            architecture=payload.get("architecture"),
            model_weight_bytes=payload.get("model_weight_bytes"),
            available_memory_bytes=payload.get("available_memory_bytes"),
            requested_context_tokens=payload.get("requested_context_tokens"),
            model_weights_already_loaded=payload.get("model_weights_already_loaded", False),
        )
        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OllamaModelMetadataError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
