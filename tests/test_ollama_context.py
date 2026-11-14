from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ollama_context import OllamaModelMetadataError, select_context_window, supports_tool_calling

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "app" / "ollama_context.py"
GIB = 1024 ** 3


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
        "model_weight_bytes": 5 * GIB,
        "available_memory_bytes": 20 * GIB,
        "requested_context_tokens": 100000,
    }


def test_context_selection_uses_model_and_live_memory_without_fixed_token_floor():
    result = select_context_window(**payload())
    assert result["maximum_context_tokens"] == 131072
    assert result["hardware_maximum_context_tokens"] == 47104
    assert result["selected_context_tokens"] == 47104
    assert result["selected_context_tokens"] <= result["requested_context_tokens"]


def test_context_selection_accepts_model_context_below_former_codex_minimum():
    data = payload()
    data["model_info"]["llama.context_length"] = 8192
    data["requested_context_tokens"] = 4096
    result = select_context_window(**data)
    assert result["maximum_context_tokens"] == 8192
    assert result["selected_context_tokens"] == 4096


def test_context_selection_uses_model_maximum_without_an_application_token_ceiling():
    data = payload()
    data["model_info"].update({
        "llama.context_length": 2097152,
        "llama.block_count": 1,
        "llama.attention.head_count_kv": 1,
        "llama.attention.key_length": 64,
        "llama.attention.value_length": 64,
    })
    data["model_weight_bytes"] = GIB
    data["available_memory_bytes"] = 100 * GIB
    data["requested_context_tokens"] = None
    result = select_context_window(**data)
    assert result["selected_context_tokens"] == 2097152
    assert result["selected_context_tokens"] == result["maximum_context_tokens"]


def test_context_selection_fails_closed_when_memory_cannot_hold_model():
    data = payload()
    data["available_memory_bytes"] = 4 * GIB
    with pytest.raises(OllamaModelMetadataError, match="cannot hold model weights"):
        select_context_window(**data)


def test_context_selection_requires_architecture_specific_kv_metadata():
    data = payload()
    data["model_info"].pop("llama.attention.head_count_kv")
    with pytest.raises(OllamaModelMetadataError, match="head_count_kv"):
        select_context_window(**data)


def test_context_selection_rejects_non_positive_requested_context():
    data = payload()
    data["requested_context_tokens"] = 0
    with pytest.raises(OllamaModelMetadataError, match="positive integer"):
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
    assert json.loads(result.stdout)["selected_context_tokens"] == 47104
