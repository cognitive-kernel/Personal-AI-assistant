from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ollama_context import MAX_CODEX_CONTEXT_TOKENS, OllamaModelMetadataError, select_context_window, supports_tool_calling

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "app" / "ollama_context.py"


def payload() -> dict:
    return {
        "model_info": {
            "llama.context_length": 131072,
            "llama.block_count": 32,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
            "llama.attention.value_length": 128,
        },
        "architecture": "llama",
        "model_weight_bytes": 5 * 1024**3,
        "available_memory_bytes": 20 * 1024**3,
        "requested_context_tokens": 100000,
    }


def test_context_selection_uses_safe_minimum_of_request_model_and_hardware():
    result = select_context_window(**payload())
    assert result["maximum_context_tokens"] == 131072
    assert result["hardware_maximum_context_tokens"] == 86016
    assert result["effective_maximum_context_tokens"] == 86016
    assert result["selected_context_tokens"] == 86016
    assert result["selected_context_tokens"] <= result["requested_context_tokens"]


def test_recorded_i5_12400_gpt_oss_host_profile_supports_upstream_64k_context():
    gib = 1024**3
    result = select_context_window(
        model_info={
            "gptoss.context_length": 131072,
            "gptoss.block_count": 24,
            "gptoss.attention.head_count_kv": 8,
            "gptoss.attention.key_length": 64,
            "gptoss.attention.value_length": 64,
        },
        architecture="gptoss",
        model_weight_bytes=13_793_441_566,
        available_memory_bytes=int(22.42 * gib),
        requested_context_tokens=131072,
        model_weights_already_loaded=False,
    )

    assert result["kv_cache_bytes_per_token"] == 49_152
    assert result["selected_context_tokens"] == 93_699
    assert result["selected_context_tokens"] >= 64_000
    assert result["selected_context_tokens"] <= 131_072
    assert result["model_weight_charge_bytes"] == 13_793_441_566


def test_context_selection_does_not_double_charge_already_loaded_model_weights():
    unloaded = payload()
    unloaded_result = select_context_window(**unloaded)

    loaded = payload()
    loaded["available_memory_bytes"] -= loaded["model_weight_bytes"]
    loaded["model_weights_already_loaded"] = True
    loaded_result = select_context_window(**loaded)

    assert loaded_result["hardware_maximum_context_tokens"] == unloaded_result["hardware_maximum_context_tokens"]
    assert loaded_result["model_weight_charge_bytes"] == 0
    assert unloaded_result["model_weight_charge_bytes"] == unloaded["model_weight_bytes"]


def test_context_selection_adapts_to_hardware_capacity_below_64k():
    data = payload()
    bytes_per_token = 32 * 8 * (128 + 128) * 2
    overhead = 512 * 1024**2
    data["available_memory_bytes"] = (
        4 * 1024**3 + data["model_weight_bytes"] + overhead + bytes_per_token * 10000
    )
    data["requested_context_tokens"] = 64000

    result = select_context_window(**data)

    assert result["hardware_maximum_context_tokens"] == 10000
    assert result["maximum_context_tokens"] == 131072
    assert result["effective_maximum_context_tokens"] == 10000
    assert result["selected_context_tokens"] == 10000


def test_context_selection_adapts_to_model_limit_below_64k():
    data = payload()
    data["model_info"]["llama.context_length"] = 8192
    data["requested_context_tokens"] = 64000

    result = select_context_window(**data)

    assert result["hardware_maximum_context_tokens"] == 86016
    assert result["maximum_context_tokens"] == 8192
    assert result["effective_maximum_context_tokens"] == 8192
    assert result["selected_context_tokens"] == 8192


def test_codex_catalog_ceiling_matches_signed_64_bit_model_metadata_schema():
    result = select_context_window(**payload())

    assert MAX_CODEX_CONTEXT_TOKENS == ((1 << 63) - 1) // 9
    assert result["codex_maximum_context_tokens"] == ((1 << 63) - 1) // 9


def test_context_selection_clamps_requested_value_to_both_hard_limits():
    data = payload()
    data["requested_context_tokens"] = 1048576

    result = select_context_window(**data)

    assert result["selected_context_tokens"] == min(
        result["maximum_context_tokens"],
        result["hardware_maximum_context_tokens"],
        result["codex_maximum_context_tokens"],
    )


def test_context_selection_fails_when_hardware_cannot_supply_any_context_token():
    data = payload()
    data["available_memory_bytes"] = (
        4 * 1024**3 + data["model_weight_bytes"] + 512 * 1024**2
    )
    with pytest.raises(OllamaModelMetadataError, match="cannot hold the model weights"):
        select_context_window(**data)


def test_context_selection_rejects_invalid_requested_context():
    data = payload()
    data["requested_context_tokens"] = 0
    with pytest.raises(OllamaModelMetadataError, match="positive integer"):
        select_context_window(**data)



def test_context_selection_uses_a_single_token_when_that_is_the_verified_capacity():
    data = payload()
    bytes_per_token = 32 * 8 * (128 + 128) * 2
    data["model_info"]["llama.context_length"] = 1
    data["available_memory_bytes"] = (
        4 * 1024**3 + data["model_weight_bytes"] + 512 * 1024**2 + bytes_per_token
    )
    data["requested_context_tokens"] = 64000

    result = select_context_window(**data)

    assert result["hardware_maximum_context_tokens"] == 1
    assert result["maximum_context_tokens"] == 1
    assert result["effective_maximum_context_tokens"] == 1
    assert result["selected_context_tokens"] == 1


def test_context_selection_requires_architecture_specific_kv_metadata():
    data = payload()
    data["model_info"].pop("llama.attention.head_count_kv")
    with pytest.raises(OllamaModelMetadataError, match="head_count_kv"):
        select_context_window(**data)


def test_tool_calling_capability_is_explicit():
    assert supports_tool_calling(["completion", "tools"])
    assert not supports_tool_calling(["completion"])
    assert not supports_tool_calling(None)


def test_context_helper_reads_json_from_explicit_file(tmp_path: Path):
    input_file = tmp_path / "context-selection.json"
    input_file.write_text(json.dumps(payload()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HELPER), "--input-file", str(input_file)],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["selected_context_tokens"] == 86016

def test_codex_context_minimum_is_separate_from_general_chat_minimum():
    from app.hardware import runtime_limits
    from app.ollama_context import MIN_CONTEXT_TOKENS

    assert MIN_CONTEXT_TOKENS == 1
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 1},
        {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 8},
    )
    assert limits["num_ctx"] == 512
