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
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 25,
        "gpu_devices": [], "gpu_available": False,
    })
    result = ollama.model_details("llama:8b")
    assert result["maximum_context_tokens"] == 131072
    assert result["recommended_context_tokens"] == 8192
    assert result["recommended_context_tokens"] <= result["maximum_context_tokens"]


def test_model_details_uses_conservative_context_when_ram_is_low(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda path, payload=None, timeout=None: (
        {
            "details": {"family": "llama"},
            "model_info": {"llama.context_length": 32768},
        } if path == "/api/show" else {"models": []}
    ))
    monkeypatch.setattr(ollama, "list_models", lambda: [{"name": "llama:8b", "size": 5_000_000_000}])
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 8, "available_ram_gb": 5,
        "gpu_devices": [], "gpu_available": False,
    })
    result = ollama.model_details("llama:8b")
    assert result["recommended_context_tokens"] == 2048
