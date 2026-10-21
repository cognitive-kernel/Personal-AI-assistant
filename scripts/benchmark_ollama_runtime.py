#!/usr/bin/env python3
"""Measure repeatable local Ollama latency and resource snapshots without recording prompts."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import hardware, ollama
from app.db import get_settings

BENCHMARK_VERSION = "v1"
CASES = (
    ("short_reasoning_en", "Explain in two concise sentences why unit tests improve software reliability."),
    ("short_reasoning_fa", "در دو جمله کوتاه توضیح بده چرا نگه‌داشتن حاشیهٔ امن RAM برای سیستم مهم است."),
    ("small_python_task", "Write a Python function that returns the factorial of a non-negative integer. Include a short docstring."),
)
KEEP_ALIVE_CHOICES = ("auto", "0", "1m", "5m", "10m", "30m")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value != value or value == float("inf") or value == float("-inf"):
        return None
    return float(value)


def response_metrics(response: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
    """Normalize Ollama's nanosecond counters and wall-clock time into a stable report."""
    load_ns = _finite_number(response.get("load_duration"))
    prompt_ns = _finite_number(response.get("prompt_eval_duration"))
    eval_ns = _finite_number(response.get("eval_duration"))
    prompt_tokens = _finite_number(response.get("prompt_eval_count"))
    output_tokens = _finite_number(response.get("eval_count"))
    return {
        "wall_time_ms": round(max(0.0, elapsed_seconds) * 1000, 2),
        "model_load_ms": round(load_ns / 1_000_000, 2) if load_ns is not None else None,
        "prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "prompt_eval_ms": round(prompt_ns / 1_000_000, 2) if prompt_ns is not None else None,
        "generation_ms": round(eval_ns / 1_000_000, 2) if eval_ns is not None else None,
        "generation_tokens_per_second": (
            round(output_tokens / (eval_ns / 1_000_000_000), 3)
            if output_tokens is not None and eval_ns is not None and eval_ns > 0
            else None
        ),
        "done": response.get("done") if isinstance(response.get("done"), bool) else None,
    }


def resource_snapshot() -> dict[str, Any]:
    detected = hardware.detect_hardware()
    cpu_percent = None
    ram_percent = None
    if hardware.psutil is not None:
        cpu_percent = _finite_number(hardware.psutil.cpu_percent(interval=0.1))
        memory = hardware.psutil.virtual_memory()
        ram_percent = _finite_number(getattr(memory, "percent", None))
        available = _finite_number(getattr(memory, "available", None))
        if available is not None:
            detected["available_ram_gb"] = round(available / (1024**3), 2)
    return {
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "ram_percent": round(ram_percent, 1) if ram_percent is not None else None,
        "logical_cpu_count": detected.get("logical_cpu_count"),
        "total_ram_gb": detected.get("total_ram_gb"),
        "available_ram_gb": detected.get("available_ram_gb"),
        "gpu_available": detected.get("gpu_available"),
        "gpu_utilization_percent": detected.get("gpu_utilization_percent"),
        "gpu_memory_percent": detected.get("gpu_memory_percent"),
        "gpu_telemetry": detected.get("gpu_telemetry"),
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) == 40 else None


def run_benchmark(
    *,
    purpose: str = "chat",
    repeats: int = 2,
    keep_alive_override: str | None = None,
) -> dict[str, Any]:
    if purpose not in {"chat", "learning", "programming"}:
        raise ValueError("purpose must be chat, learning, or programming.")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10.")
    if keep_alive_override is not None and keep_alive_override not in KEEP_ALIVE_CHOICES:
        raise ValueError("keep_alive_override is not an allowed Ollama duration.")

    settings = get_settings()
    model = str(settings.get(f"{purpose}_ollama_model", settings.get("ollama_model", ""))).strip()
    if not model:
        raise RuntimeError(f"No Ollama model is configured for {purpose}.")
    inventory = ollama.list_models()
    model_record = next((item for item in inventory if item.get("name") == model), None)
    before = resource_snapshot()
    limits = hardware.runtime_limits({
        "ollama_num_ctx": settings.get(f"{purpose}_ollama_num_ctx", settings.get("ollama_num_ctx", 2048)),
        "ollama_num_thread": settings.get(f"{purpose}_ollama_num_thread", settings.get("ollama_num_thread", 0)),
        "cpu_resource_limit_percent": settings.get("cpu_resource_limit_percent", 90),
        "ram_budget_max_gb": settings.get("ram_budget_max_gb", 25),
        "ram_safety_reserve_gb": settings.get("ram_safety_reserve_gb", 4),
    }, {
        "logical_cpu_count": before["logical_cpu_count"],
        "total_ram_gb": before["total_ram_gb"],
        "available_ram_gb": before["available_ram_gb"],
    })
    residency_settings = settings
    if keep_alive_override == "auto":
        residency_settings = {**settings, f"{purpose}_model_keep_alive": "auto"}
    keep_alive = (
        hardware.model_keep_alive(purpose, {
            "total_ram_gb": before["total_ram_gb"],
            "available_ram_gb": before["available_ram_gb"],
        }, residency_settings)
        if keep_alive_override in (None, "auto")
        else keep_alive_override
    )
    records = []
    for case_id, prompt in CASES:
        for repeat in range(1, repeats + 1):
            started = time.perf_counter()
            response = ollama._request("/api/chat", {
                "model": model,
        "model_digest": model_record.get("digest") if isinstance(model_record, dict) else None,
        "model_size_bytes": model_record.get("size") if isinstance(model_record, dict) and isinstance(model_record.get("size"), int) else None,
                "stream": False,
                "keep_alive": keep_alive,
                "messages": [{"role": "user", "content": prompt}],
                "options": {
                    "num_ctx": limits["num_ctx"],
                    "num_thread": limits["cpu_threads"],
                },
            }, timeout=int(settings.get(f"{purpose}_ollama_timeout_seconds", settings.get("ollama_timeout_seconds", 300))))
            elapsed = time.perf_counter() - started
            records.append({
                "case_id": case_id,
                "repeat": repeat,
                **response_metrics(response, elapsed),
            })
    after = resource_snapshot()
    valid_rates = [row["generation_tokens_per_second"] for row in records if row["generation_tokens_per_second"] is not None]
    wall_times = [row["wall_time_ms"] for row in records]
    load_times = [row["model_load_ms"] for row in records if row["model_load_ms"] is not None]
    return {
        "schema_version": 1,
        "benchmark": "ollama-runtime",
        "dataset_version": BENCHMARK_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "python_version": platform.python_version(),
        "purpose": purpose,
        "model": model,
        "gpu_enabled_setting": settings.get("gpu_enabled") is True,
        "keep_alive": keep_alive,
        "repeats_per_case": repeats,
        "runtime_limits": limits,
        "resource_before": before,
        "resource_after": after,
        "summary": {
            "runs": len(records),
            "median_wall_time_ms": round(statistics.median(wall_times), 2) if wall_times else None,
            "median_model_load_ms": round(statistics.median(load_times), 2) if load_times else None,
            "median_generation_tokens_per_second": round(statistics.median(valid_rates), 3) if valid_rates else None,
        },
        "runs": records,
        "interpretation": [
            "Compare reports only when model digest/version, purpose, dataset version, context, thread settings, and keep_alive are equivalent.",
            "Timing and hardware snapshots are observational; they do not establish answer quality.",
            "No prompts, generated text, host name, or local file paths are stored in this report.",
            "GPU utilization is unknown when supported telemetry is unavailable; the benchmark does not infer GPU use from absence of telemetry.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", choices=("chat", "learning", "programming"), default="chat")
    parser.add_argument("--repeats", type=int, default=2, help="repeats per fixed prompt (1-10)")
    parser.add_argument("--keep-alive", choices=KEEP_ALIVE_CHOICES, default=None, help="override profile setting; auto uses the configured RAM-aware policy")
    parser.add_argument("--output", type=Path, help="optional report path; otherwise JSON is printed to stdout")
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(purpose=args.purpose, repeats=args.repeats, keep_alive_override=args.keep_alive)
    except Exception as exc:
        print(f"Benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Wrote redacted benchmark report: {args.output}")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
