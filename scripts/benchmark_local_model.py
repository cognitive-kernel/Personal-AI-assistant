#!/usr/bin/env python3
"""Reproducible local Ollama latency/throughput benchmark for target-host acceptance.

Only the configured loopback Ollama endpoint is used. The report omits prompts,
generated text, credentials, and raw model output. A passing result is a
performance measurement, not a lesson-quality or scientific-correctness claim.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import get_settings
from app.ollama import OllamaError, _request


PROMPT = (
    "Write a concise technical explanation in Persian of why a database query plan "
    "should be measured on representative data. Give exactly five numbered points. "
    "Do not include URLs or mention this benchmark."
)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _rate(response: dict[str, Any], elapsed_seconds: float) -> tuple[int, float]:
    count = response.get("eval_count")
    duration_ns = response.get("eval_duration")
    if (
        isinstance(count, bool) or not isinstance(count, int) or count < 1
        or isinstance(duration_ns, bool) or not isinstance(duration_ns, (int, float))
        or not math.isfinite(duration_ns) or duration_ns <= 0
    ):
        raise ValueError("Ollama response omitted valid generation token metrics.")
    return count, count / (duration_ns / 1_000_000_000)


def run_benchmark(
    *, runs: int = 3, min_tokens_per_second: float = 1.0,
    max_latency_seconds: float = 120.0,
) -> dict[str, Any]:
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= 10:
        raise ValueError("runs must be an integer from 1 to 10.")
    if (
        isinstance(min_tokens_per_second, bool)
        or not isinstance(min_tokens_per_second, (int, float))
        or not math.isfinite(min_tokens_per_second) or min_tokens_per_second <= 0
    ):
        raise ValueError("min_tokens_per_second must be finite and positive.")
    if (
        isinstance(max_latency_seconds, bool)
        or not isinstance(max_latency_seconds, (int, float))
        or not math.isfinite(max_latency_seconds) or max_latency_seconds <= 0
    ):
        raise ValueError("max_latency_seconds must be finite and positive.")

    settings = get_settings()
    model = str(settings["ollama_model"])
    tags = _request("/api/tags")
    models = tags.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama tags response has an invalid shape.")
    match = next(
        (item for item in models if isinstance(item, dict) and item.get("name") == model),
        None,
    )
    if match is None:
        return {
            "schema_version": 1, "benchmark": "local-ollama-generation",
            "status": "model_not_found", "model": model, "git_commit": _git_commit(),
            "runs_requested": runs, "runs_completed": 0,
            "performance_threshold_passed": False,
            "interpretation": "Configured model tag was not present in local Ollama model inventory.",
        }

    samples: list[dict[str, Any]] = []
    # Warm-up is deliberately excluded from measured samples.
    for index in range(runs + 1):
        started = time.perf_counter()
        response = _request("/api/generate", {
            "model": model, "prompt": PROMPT, "stream": False,
            "options": {"temperature": 0, "num_predict": 128},
        })
        elapsed = time.perf_counter() - started
        generated = response.get("response")
        if not isinstance(generated, str) or not generated.strip():
            raise ValueError("Ollama returned no generated text.")
        count, tokens_per_second = _rate(response, elapsed)
        if index:
            samples.append({
                "latency_seconds": round(elapsed, 4),
                "generated_tokens": count,
                "generation_tokens_per_second": round(tokens_per_second, 3),
            })

    latencies = [item["latency_seconds"] for item in samples]
    rates = [item["generation_tokens_per_second"] for item in samples]
    median_latency = statistics.median(latencies)
    median_rate = statistics.median(rates)
    threshold_passed = median_latency <= max_latency_seconds and median_rate >= min_tokens_per_second
    return {
        "schema_version": 1,
        "benchmark": "local-ollama-generation",
        "status": "passed" if threshold_passed else "below_threshold",
        "model": model,
        "model_digest": match.get("digest") if isinstance(match.get("digest"), str) else None,
        "git_commit": _git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "runs_requested": runs,
        "runs_completed": len(samples),
        "median_latency_seconds": round(median_latency, 4),
        "max_latency_seconds_observed": round(max(latencies), 4),
        "median_generation_tokens_per_second": round(median_rate, 3),
        "minimum_generation_tokens_per_second": float(min_tokens_per_second),
        "maximum_allowed_latency_seconds": float(max_latency_seconds),
        "performance_threshold_passed": threshold_passed,
        "samples": samples,
        "raw_prompt_included": False,
        "generated_text_included": False,
        "interpretation": (
            "Local model performance only. This does not prove answer quality, scientific correctness, "
            "sustained-load capacity, or target-host acceptance outside this measured workload."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Measured generations, 1..10 (one warm-up is added).")
    parser.add_argument("--min-tokens-per-second", type=float, default=1.0)
    parser.add_argument("--max-latency-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_benchmark(
            runs=args.runs,
            min_tokens_per_second=args.min_tokens_per_second,
            max_latency_seconds=args.max_latency_seconds,
        )
    except (OSError, RuntimeError, ValueError, OllamaError) as exc:
        report = {
            "schema_version": 1, "benchmark": "local-ollama-generation",
            "status": "error", "error_type": type(exc).__name__,
            "performance_threshold_passed": False,
            "interpretation": "Local model benchmark could not produce a valid measurement.",
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
