from app import hardware, ollama
from scripts import benchmark_ollama_runtime as benchmark


def test_response_metrics_normalize_ollama_nanosecond_counters():
    metrics = benchmark.response_metrics(
        {
            "load_duration": 2_000_000_000,
            "prompt_eval_count": 12,
            "prompt_eval_duration": 500_000_000,
            "eval_count": 20,
            "eval_duration": 2_000_000_000,
            "done": True,
        },
        2.5,
    )
    assert metrics["wall_time_ms"] == 2500
    assert metrics["model_load_ms"] == 2000
    assert metrics["prompt_tokens"] == 12
    assert metrics["output_tokens"] == 20
    assert metrics["prompt_eval_ms"] == 500
    assert metrics["generation_ms"] == 2000
    assert metrics["generation_tokens_per_second"] == 10
    assert metrics["done"] is True


def test_response_metrics_handles_missing_or_invalid_counters():
    metrics = benchmark.response_metrics(
        {"load_duration": -1, "eval_count": "unknown", "eval_duration": 0},
        0.25,
    )
    assert metrics["wall_time_ms"] == 250
    assert metrics["model_load_ms"] is None
    assert metrics["output_tokens"] is None
    assert metrics["generation_tokens_per_second"] is None


def test_run_benchmark_emits_redacted_fixed_workload_report(monkeypatch):
    settings = {
        "chat_ollama_model": "example-model:latest",
        "chat_ollama_timeout_seconds": 30,
        "chat_ollama_num_ctx": 2048,
        "chat_ollama_num_thread": 2,
        "gpu_enabled": False,
        "chat_model_keep_alive": "auto",
    }
    monkeypatch.setattr(benchmark, "get_settings", lambda: settings)
    monkeypatch.setattr(ollama, "list_models", lambda: [
        {"name": "example-model:latest", "digest": "abc123", "size": 1234}
    ])
    snapshots = iter([
        {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 12, "cpu_percent": 5.0,
         "ram_percent": 25.0, "gpu_available": False, "gpu_utilization_percent": None,
         "gpu_memory_percent": None, "gpu_telemetry": "unavailable"},
        {"logical_cpu_count": 4, "total_ram_gb": 16, "available_ram_gb": 11, "cpu_percent": 15.0,
         "ram_percent": 30.0, "gpu_available": False, "gpu_utilization_percent": None,
         "gpu_memory_percent": None, "gpu_telemetry": "unavailable"},
    ])
    monkeypatch.setattr(benchmark, "resource_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(hardware, "runtime_limits", lambda settings, detected: {"cpu_threads": 2, "num_ctx": 2048})
    monkeypatch.setattr(hardware, "model_keep_alive", lambda purpose, detected, settings: "1m")
    monkeypatch.setattr(benchmark, "_git_commit", lambda: "a" * 40)
    calls = []
    def fake_request(path, payload, timeout):
        calls.append((path, payload, timeout))
        return {
            "load_duration": 1_000_000_000,
            "prompt_eval_count": 10,
            "prompt_eval_duration": 100_000_000,
            "eval_count": 5,
            "eval_duration": 500_000_000,
            "done": True,
        }
    monkeypatch.setattr(ollama, "_request", fake_request)

    report = benchmark.run_benchmark(purpose="chat", repeats=1)
    assert report["benchmark"] == "ollama-runtime"
    assert report["model_digest"] == "abc123"
    assert report["model_size_bytes"] == 1234
    assert report["summary"]["runs"] == len(benchmark.CASES)
    assert len(calls) == len(benchmark.CASES)
    assert all(call[0] == "/api/chat" for call in calls)
    assert all(call[1]["options"]["num_gpu"] is not True for call in calls)
    serialized = __import__("json").dumps(report)
    assert "Explain in two concise sentences" not in serialized
    assert "Write a Python function" not in serialized


def test_run_benchmark_rejects_out_of_range_repeats():
    import pytest
    with pytest.raises(ValueError, match="between 1 and 10"):
        benchmark.run_benchmark(repeats=0)
