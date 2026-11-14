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


def maximum_context_tokens(model_info: dict[str, Any], architecture: str) -> int:
    if not isinstance(model_info, dict) or not isinstance(architecture, str) or not architecture.strip():
        raise OllamaModelMetadataError("Model architecture/context metadata is unavailable.")
    value = model_info.get(f"{architecture}.context_length")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OllamaModelMetadataError(
            f"Model metadata does not expose a valid {architecture}.context_length."
        )
    return value


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
) -> dict[str, int | None]:
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
        )
        json.dump(result, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OllamaModelMetadataError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
