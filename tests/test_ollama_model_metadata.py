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
    assert result["recommended_context_tokens"] == 32768
    assert result["recommended_context_tokens"] <= result["maximum_context_tokens"]
    assert result["memory_estimate"]["kv_cache_dtype"].startswith("unknown; FP32 upper bound")
    assert result["memory_estimate"]["kv_cache_bytes_per_token_upper_bound"] == 32 * 8 * 256 * 4


def test_model_details_uses_conservative_context_when_ram_is_low(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: (
        {
            "details": {"family": "llama"},
            "model_info": {"llama.context_length": 32768},
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
                "model_info": {
                    "llama.context_length": 8192,
                    "llama.block_count": 32,
                    "llama.attention.head_count_kv": 8,
                    "llama.attention.key_length": 128,
                    "llama.attention.value_length": 128,
                },
            }
        if path == "/api/chat":
            return {"message": {"content": "ok"}}
        raise AssertionError(path)

    monkeypatch.setattr(ollama, "_request", fake_request)
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "llama:8b", "size": 5_000_000_000}])

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



def test_chat_refuses_when_hardware_safe_context_cannot_be_estimated(monkeypatch):
    calls = []
    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "chat_ollama_model": "unknown-architecture:latest",
        "chat_ollama_num_ctx": 8192,
        "chat_ollama_num_thread": 0,
        "cpu_resource_limit_percent": 90,
        "ram_budget_max_gb": 25,
        "ram_safety_reserve_gb": 4,
        "gpu_enabled": False,
    })
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "available_ram_gb": 24, "gpu_devices": [],
    })
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "unknown-architecture:latest", "size": 2_000_000_000}])
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *args: "0")

    def fake_request(path, payload=None, timeout=None):
        calls.append(path)
        if path == "/api/show":
            return {
                "details": {"family": "unknown"},
                "model_info": {"unknown.context_length": 32768},
            }
        raise AssertionError("No inference may run without a verified hardware-safe context.")

    monkeypatch.setattr(ollama, "_request", fake_request)
    import pytest
    with pytest.raises(ollama.OllamaError, match="Cannot calculate a hardware-safe context"):
        ollama.chat("hello")
    assert calls == ["/api/show"]


def test_intel_shared_gpu_memory_does_not_inflate_safe_context(monkeypatch):
    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "ram_budget_max_gb": 25, "ram_safety_reserve_gb": 4, "gpu_enabled": True,
    })
    hardware = {
        "available_ram_gb": 8,
        "gpu_devices": [{
            "vendor": "Intel", "name": "Intel UHD Graphics 730",
            "device_type": "integrated", "memory_free_mb": 8192,
        }],
    }
    estimate = {
        "weights_gib": 2.0,
        "overhead_gib": 0.5,
        "kv_cache_bytes_per_token_upper_bound": 1024 * 1024,
    }
    assert ollama._recommended_context_tokens(32768, estimate, hardware) == 1024


def test_safe_context_is_unavailable_when_calculated_fit_is_below_minimum(monkeypatch):
    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "ram_budget_max_gb": 25, "ram_safety_reserve_gb": 4, "gpu_enabled": False,
    })
    hardware = {"available_ram_gb": 5, "gpu_devices": []}
    estimate = {
        "weights_gib": 1.0,
        "overhead_gib": 0.5,
        "kv_cache_bytes_per_token_upper_bound": 1024 * 1024 * 1024,
    }
    assert ollama._recommended_context_tokens(32768, estimate, hardware) is None


def test_model_context_metadata_prefers_architecture_and_uses_conservative_fallback():
    model_info = {
        "llama.context_length": 32768,
        "vision.context_length": 4096,
    }
    assert ollama._model_context_metadata(model_info, "llama") == 32768
    assert ollama._model_context_metadata(model_info, "unknown") == 4096

def test_integrated_intel_gpu_shared_memory_is_not_counted_as_dedicated_vram():
    hardware = {
        "gpu_devices": [{
            "vendor": "Intel",
            "name": "Intel UHD Graphics 730",
            "device_type": "integrated",
            "memory_free_mb": 8192,
        }]
    }
    assert ollama._dedicated_gpu_budget_gb(hardware) is None


def test_discrete_gpu_uses_only_measured_dedicated_vram():
    hardware = {
        "gpu_devices": [{
            "vendor": "NVIDIA",
            "name": "GeForce RTX",
            "device_type": "discrete",
            "memory_free_mb": 6144,
            "usable_memory_free_mb": 12288,
        }]
    }
    assert ollama._dedicated_gpu_budget_gb(hardware) == 6.0

