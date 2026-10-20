import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_local_model.py"
SPEC = importlib.util.spec_from_file_location("benchmark_local_model", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(benchmark)


def test_local_model_benchmark_records_redacted_performance_metrics(monkeypatch):
    monkeypatch.setattr(benchmark, "get_settings", lambda: {"ollama_model": "model:latest"})
    calls = {"generate": 0}

    def fake_request(path, payload=None):
        if path == "/api/tags":
            return {"models": [{"name": "model:latest", "digest": "sha256:abc"}]}
        calls["generate"] += 1
        return {
            "response": "private generated lesson content",
            "eval_count": 100,
            "eval_duration": 5_000_000_000,
        }

    monkeypatch.setattr(benchmark, "_request", fake_request)
    monkeypatch.setattr(benchmark, "_git_commit", lambda: "commit")

    report = benchmark.run_benchmark(runs=3, min_tokens_per_second=10, max_latency_seconds=120)

    assert calls["generate"] == 4
    assert report["status"] == "passed"
    assert report["model_digest"] == "sha256:abc"
    assert report["median_generation_tokens_per_second"] == 20.0
    assert report["runs_completed"] == 3
    assert report["generated_text_included"] is False
    assert "private generated lesson content" not in str(report)


def test_local_model_benchmark_fails_closed_when_configured_model_is_missing(monkeypatch):
    monkeypatch.setattr(benchmark, "get_settings", lambda: {"ollama_model": "missing:latest"})
    monkeypatch.setattr(benchmark, "_request", lambda *_args, **_kwargs: {"models": []})

    report = benchmark.run_benchmark()

    assert report["status"] == "model_not_found"
    assert report["runs_completed"] == 0
    assert report["performance_threshold_passed"] is False


@pytest.mark.parametrize("runs", [0, 11, True])
def test_local_model_benchmark_rejects_invalid_run_counts(monkeypatch, runs):
    with pytest.raises(ValueError, match="runs"):
        benchmark.run_benchmark(runs=runs)
