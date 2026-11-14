from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
MODEL_OVERHEAD_RATIO = 0.10
DEFAULT_MEMORY_RESERVE_RATIO = 0.15


class OllamaModelMetadataError(ValueError):
    """Raised when model or host metadata is insufficient for a safe context choice."""


def resolve_model_architecture(model_info: dict[str, Any], architecture: str | None) -> str:
    if not isinstance(model_info, dict):
        raise OllamaModelMetadataError("Model architecture/context metadata is unavailable.")
    candidates = []
    if isinstance(architecture, str) and architecture.strip():
        candidates.append(architecture.strip())
    declared = model_info.get("general.architecture")
    if isinstance(declared, str) and declared.strip() and declared.strip() not in candidates:
        candidates.append(declared.strip())
    for candidate in candidates:
        value = model_info.get(f"{candidate}.context_length")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return candidate
    raise OllamaModelMetadataError(
        "Model metadata does not expose a valid architecture-specific context_length."
    )


def maximum_context_tokens(model_info: dict[str, Any], architecture: str | None) -> int:
    resolved_architecture = resolve_model_architecture(model_info, architecture)
    return model_info[f"{resolved_architecture}.context_length"]


def supports_tool_calling(capabilities: Any) -> bool:
    return isinstance(capabilities, list) and any(
        isinstance(value, str) and value.casefold() == "tools"
        for value in capabilities
    )


def select_context_window(
    *,
    model_info: dict[str, Any],
    architecture: str,
    model_weight_bytes: int,
    available_memory_bytes: int,
    requested_context_tokens: int | None = None,
    reserve_bytes: int | None = None,
    model_is_loaded: bool = False,
    running_context_tokens: int | None = None,
) -> dict[str, int | bool | None]:
    """Select the exact safe context from model metadata and current host capacity.

    No application-level token floor or ceiling is imposed. The model's declared
    context length and measured memory budget are the only operational limits.
    KV-cache memory is budgeted at four bytes per element as a conservative
    upper bound because Ollama does not consistently expose its runtime dtype.
    """
    maximum = maximum_context_tokens(model_info, architecture)
    numeric_inputs = {
        "model_weight_bytes": model_weight_bytes,
        "available_memory_bytes": available_memory_bytes,
    }
    for name, value in numeric_inputs.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OllamaModelMetadataError(f"{name} must be a positive integer.")

    if requested_context_tokens is not None and (
        isinstance(requested_context_tokens, bool)
        or not isinstance(requested_context_tokens, int)
        or requested_context_tokens <= 0
    ):
        raise OllamaModelMetadataError("Requested context must be a positive integer.")
    if not isinstance(model_is_loaded, bool):
        raise OllamaModelMetadataError("model_is_loaded must be a boolean.")
    if model_is_loaded and (
        isinstance(running_context_tokens, bool)
        or not isinstance(running_context_tokens, int)
        or running_context_tokens <= 0
    ):
        raise OllamaModelMetadataError(
            "A loaded model must expose its currently allocated positive context length."
        )

    if reserve_bytes is None:
        effective_reserve = int(available_memory_bytes * DEFAULT_MEMORY_RESERVE_RATIO)
    elif isinstance(reserve_bytes, bool) or not isinstance(reserve_bytes, int) or reserve_bytes < 0:
        raise OllamaModelMetadataError("reserve_bytes must be a non-negative integer.")
    else:
        effective_reserve = reserve_bytes

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
        * 4
    )
    if bytes_per_token <= 0:
        raise OllamaModelMetadataError("KV-cache memory estimate is invalid.")

    overhead_bytes = int(model_weight_bytes * MODEL_OVERHEAD_RATIO)
    if model_is_loaded:
        # Current free memory already excludes the resident weights and KV cache.
        # Add back only the measured current KV allocation to derive total cache
        # capacity without subtracting resident weights a second time.
        allocated_cache_bytes = bytes_per_token * int(running_context_tokens)
        cache_budget_bytes = (
            available_memory_bytes
            - effective_reserve
            - overhead_bytes
            + allocated_cache_bytes
        )
    else:
        cache_budget_bytes = (
            available_memory_bytes
            - effective_reserve
            - model_weight_bytes
            - overhead_bytes
        )
    if cache_budget_bytes <= 0:
        raise OllamaModelMetadataError(
            "Available memory cannot hold model weights, overhead, and the dynamic safety reserve."
        )

    hardware_maximum = min(maximum, cache_budget_bytes // bytes_per_token)
    if hardware_maximum <= 0:
        raise OllamaModelMetadataError(
            "Available memory cannot safely provide even the model's smallest positive context allocation."
        )

    requested = requested_context_tokens
    selected = min(requested if requested is not None else maximum, maximum, hardware_maximum)
    return {
        "maximum_context_tokens": maximum,
        "hardware_maximum_context_tokens": hardware_maximum,
        "requested_context_tokens": requested,
        "selected_context_tokens": selected,
        "kv_cache_bytes_per_token": bytes_per_token,
        "model_weight_bytes": model_weight_bytes,
        "available_memory_bytes": available_memory_bytes,
        "reserve_bytes": effective_reserve,
        "overhead_bytes": overhead_bytes,
        "model_is_loaded": model_is_loaded,
        "running_context_tokens": running_context_tokens,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a safe Ollama context window.")
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
            reserve_bytes=payload.get("reserve_bytes"),
            model_is_loaded=payload.get("model_is_loaded", False),
            running_context_tokens=payload.get("running_context_tokens"),
        )
        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OllamaModelMetadataError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
