from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ollama_context import OllamaModelMetadataError, select_context_window, supports_tool_calling

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
        "model_weight_bytes": 5368709120,
        "available_memory_bytes": 21474836480,
        "requested_context_tokens": 100000,
    }


def test_context_selection_uses_exact_safe_minimum():
    result = select_context_window(**payload())
    assert result["maximum_context_tokens"] == 131072
    assert result["hardware_maximum_context_tokens"] == 86016
    assert result["selected_context_tokens"] == 86016
    assert result["selected_context_tokens"] <= result["requested_context_tokens"]


def test_context_selection_fails_closed_when_memory_is_insufficient():
    data = payload()
    data["available_memory_bytes"] = 10 * 1024**3
    with pytest.raises(OllamaModelMetadataError, match="safely support"):
        select_context_window(**data)


def test_context_selection_requires_architecture_specific_kv_metadata():
    data = payload()
    data["model_info"].pop("llama.attention.head_count_kv")
    with pytest.raises(OllamaModelMetadataError, match="head_count_kv"):
        select_context_window(**data)


def test_tool_calling_capability_is_explicit():
    assert supports_tool_calling(["completion", "tools"])
    assert not supports_tool_calling(["completion"])


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
