from app import ollama


def test_model_details_reads_architecture_context_and_estimates_selected_context(monkeypatch):
    calls = []
    def fake_request(path, payload=None, timeout=None):
        calls.append((path, payload))
        if path == "/api/show":
            return {
                "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M", "family": "llama"},
                "model_info": {
                    "llama.context_length": 131072,
                    "llama.block_count": 32,
                    "llama.attention.head_count_kv": 8,
                    "llama.attention.key_length": 128,
                    "llama.attention.value_length": 128,
                },
            }
        if path == "/api/ps":
            return {"models": [{"name": "llama:8b", "context_length": 8192, "size": 5_000_000_000, "size_vram": 1_000_000_000}]}
        raise AssertionError(path)
    monkeypatch.setattr(ollama, "_request", fake_request)
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "llama:8b", "size": 5_000_000_000}])

    result = ollama.model_details("llama:8b", context_tokens=16384)

    assert result["maximum_context_tokens"] == 131072
    assert result["parameter_size"] == "8B"
    assert result["quantization"] == "Q4_K_M"
    assert result["memory_estimate"]["context_tokens"] == 16384
    assert result["memory_estimate"]["kv_cache_gib_float16"] is not None
    assert result["running"]["context_length"] == 8192
    assert result["running"]["size_vram_bytes"] == 1_000_000_000
    assert [path for path, _ in calls] == ["/api/show", "/api/ps"]


def test_model_details_does_not_invent_context_or_kv_estimate(monkeypatch):
    def fake_request(path, payload=None, timeout=None):
        if path == "/api/show":
            return {"details": {"parameter_size": "7B", "quantization_level": "Q4_0"}, "model_info": {}}
        if path == "/api/ps":
            return {"models": []}
        raise AssertionError(path)
    monkeypatch.setattr(ollama, "_request", fake_request)
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "model:7b", "size": 4_000_000_000}])

    result = ollama.model_details("model:7b", context_tokens=4096)

    assert result["maximum_context_tokens"] is None
    assert result["recommended_context_tokens"] is None
    assert result["context_metadata_available"] is False
    assert result["memory_estimate"] is None
    assert result["running"] is None



def test_model_details_recommends_context_within_model_and_memory_limits(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: (
        {
            "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M", "family": "llama"},
            "model_info": {
                "llama.context_length": 131072,
                "llama.block_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
        } if path == "/api/show" else {"models": []}
    ))
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "llama:8b", "size": 5_000_000_000}])
    monkeypatch.setattr(ollama, "get_settings", lambda: {"ram_budget_max_gb": 25, "ram_safety_reserve_gb": 4, "gpu_enabled": False})
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 25,
        "gpu_devices": [], "gpu_available": False,
    })
    result = ollama.model_details("llama:8b")
    assert result["maximum_context_tokens"] == 131072
    assert result["recommended_context_tokens"] == 65536
    assert result["recommended_context_tokens"] <= result["maximum_context_tokens"]


def test_model_details_uses_conservative_context_when_ram_is_low(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: (
        {
            "details": {"family": "llama"},
            "model_info": {
                "llama.context_length": 32768,
                "llama.block_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
        } if path == "/api/show" else {"models": []}
    ))
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "llama:8b", "size": 5_000_000_000}])
    monkeypatch.setattr(ollama, "get_settings", lambda: {"ram_budget_max_gb": 25, "ram_safety_reserve_gb": 4, "gpu_enabled": False})
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 8, "available_ram_gb": 5,
        "gpu_devices": [], "gpu_available": False,
    })
    result = ollama.model_details("llama:8b")
    assert result["recommended_context_tokens"] is None


def test_chat_clamps_requested_context_to_verified_model_maximum(monkeypatch):
    calls = []

    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "chat_ollama_model": "llama:8b",
        "chat_ollama_num_ctx": 65536,
        "chat_ollama_num_thread": 0,
        "cpu_resource_limit_percent": 90,
        "ram_budget_max_gb": 25,
        "ram_safety_reserve_gb": 4,
        "gpu_enabled": False,
    })
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8,
        "total_ram_gb": 32,
        "available_ram_gb": 24,
        "gpu_devices": [],
        "gpu_available": False,
    })
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *args: "5m")
    from app import usage_metrics
    monkeypatch.setattr(usage_metrics, "record_ollama_response", lambda **kwargs: None)

    def fake_request(path, payload=None, timeout=None):
        calls.append((path, payload))
        if path == "/api/show":
            return {
                "details": {"family": "llama"},
                "model_info": {"llama.context_length": 8192},
            }
        if path == "/api/chat":
            return {"message": {"content": "ok"}}
        raise AssertionError(path)

    monkeypatch.setattr(ollama, "_request", fake_request)

    assert ollama.chat("hello") == "ok"
    chat_payload = next(payload for path, payload in calls if path == "/api/chat")
    assert chat_payload["options"]["num_ctx"] == 8192


def test_chat_refuses_to_send_when_model_context_metadata_is_unavailable(monkeypatch):
    calls = []

    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "chat_ollama_model": "unknown:latest",
        "chat_ollama_num_ctx": 65536,
        "chat_ollama_num_thread": 0,
        "cpu_resource_limit_percent": 90,
        "ram_budget_max_gb": 25,
        "ram_safety_reserve_gb": 4,
    })
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8,
        "total_ram_gb": 32,
        "available_ram_gb": 24,
        "gpu_devices": [],
        "gpu_available": False,
    })

    def fake_request(path, payload=None, timeout=None):
        calls.append(path)
        if path == "/api/show":
            return {"details": {"family": "unknown"}, "model_info": {}}
        raise AssertionError("Chat request must not be sent without verified context metadata.")

    monkeypatch.setattr(ollama, "_request", fake_request)

    import pytest
    with pytest.raises(ollama.OllamaError, match="verifiable context_length"):
        ollama.chat("hello")
    assert calls == ["/api/show"]


def test_model_context_metadata_requires_exact_architecture_metadata():
    model_info = {
        "llama.context_length": 32768,
        "vision.context_length": 4096,
    }
    assert ollama._model_context_metadata(model_info, "llama") == 32768
    assert ollama._model_context_metadata(model_info, "unknown") is None


def test_codex_context_selection_uses_minimum_of_request_model_and_safe_memory():
    from app.ollama_context import select_context_window

    model_info = {
        "llama.context_length": 131072,
        "llama.block_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.attention.key_length": 128,
        "llama.attention.value_length": 128,
    }
    result = select_context_window(
        model_info=model_info,
        architecture="llama",
        model_weight_bytes=5 * 1024**3,
        available_memory_bytes=20 * 1024**3,
        requested_context_tokens=100000,
    )

    assert result["maximum_context_tokens"] == 131072
    assert result["hardware_maximum_context_tokens"] == 86016
    assert result["selected_context_tokens"] == 86016
    assert result["selected_context_tokens"] <= result["requested_context_tokens"]
    assert result["selected_context_tokens"] <= result["maximum_context_tokens"]
    assert result["selected_context_tokens"] >= 64000


def test_codex_context_selection_caps_at_catalog_supported_maximum():
    from app.ollama_context import select_context_window

    result = select_context_window(
        model_info={
            "llama.context_length": 2097152,
            "llama.block_count": 1,
            "llama.attention.head_count_kv": 1,
            "llama.attention.key_length": 64,
            "llama.attention.value_length": 64,
        },
        architecture="llama",
        model_weight_bytes=1024**3,
        available_memory_bytes=100 * 1024**3,
    )

    assert result["selected_context_tokens"] == 1048576
    assert result["codex_maximum_context_tokens"] == 1048576


def test_codex_context_selection_rejects_models_below_the_64k_minimum():
    import pytest
    from app.ollama_context import OllamaModelMetadataError, select_context_window

    with pytest.raises(OllamaModelMetadataError, match="below the Codex minimum 64000"):
        select_context_window(
            model_info={
                "llama.context_length": 8192,
                "llama.block_count": 1,
                "llama.attention.head_count_kv": 1,
                "llama.attention.key_length": 64,
                "llama.attention.value_length": 64,
            },
            architecture="llama",
            model_weight_bytes=1024**3,
            available_memory_bytes=16 * 1024**3,
            requested_context_tokens=65536,
        )


def test_codex_context_selection_fails_closed_when_hardware_cannot_supply_64k():
    import pytest
    from app.ollama_context import OllamaModelMetadataError, select_context_window

    with pytest.raises(OllamaModelMetadataError, match="minimum 64000-token context"):
        select_context_window(
            model_info={
                "llama.context_length": 131072,
                "llama.block_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
            architecture="llama",
            model_weight_bytes=1024**3,
            available_memory_bytes=6 * 1024**3,
        )


def test_codex_context_selection_fails_closed_when_model_or_hardware_metadata_is_missing():
    import pytest
    from app.ollama_context import OllamaModelMetadataError, select_context_window

    with pytest.raises(OllamaModelMetadataError, match="context_length"):
        select_context_window(
            model_info={},
            architecture="unknown",
            model_weight_bytes=1024**3,
            available_memory_bytes=16 * 1024**3,
        )

    with pytest.raises(OllamaModelMetadataError, match="attention.head_count_kv"):
        select_context_window(
            model_info={
                "llama.context_length": 131072,
                "llama.block_count": 32,
            },
            architecture="llama",
            model_weight_bytes=1024**3,
            available_memory_bytes=16 * 1024**3,
        )

    with pytest.raises(OllamaModelMetadataError, match="safety reserve"):
        select_context_window(
            model_info={
                "llama.context_length": 131072,
                "llama.block_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
            architecture="llama",
            model_weight_bytes=12 * 1024**3,
            available_memory_bytes=8 * 1024**3,
        )


def test_codex_tool_calling_requires_explicit_ollama_tools_capability():
    from app.ollama_context import supports_tool_calling

    assert supports_tool_calling(["completion", "tools", "thinking"]) is True
    assert supports_tool_calling(["completion", "thinking"]) is False
    assert supports_tool_calling(None) is False


def test_codex_context_selection_preserves_exact_minimum_without_rounding():
    from app.ollama_context import select_context_window

    result = select_context_window(
        model_info={
            "llama.context_length": 131072,
            "llama.block_count": 1,
            "llama.attention.head_count_kv": 1,
            "llama.attention.key_length": 64,
            "llama.attention.value_length": 64,
        },
        architecture="llama",
        model_weight_bytes=1024**3,
        available_memory_bytes=16 * 1024**3,
        requested_context_tokens=64001,
    )

    assert result["selected_context_tokens"] == min(
        result["requested_context_tokens"],
        result["maximum_context_tokens"],
        result["hardware_maximum_context_tokens"],
    )
    assert result["selected_context_tokens"] == 64001
