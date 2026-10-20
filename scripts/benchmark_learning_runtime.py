#!/usr/bin/env python3
"""Measure a bounded local Ollama smoke benchmark and emit a redacted JSON report.

This script performs only local loopback requests. It does not run during unit CI
and does not claim to measure the full lesson-generation workflow.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil

from app import db, ollama
from app.hardware import detect_hardware, runtime_limits


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _ollama_related_rss_bytes() -> int | None:
    """Best-effort sampled RSS for processes whose names identify Ollama/model runners."""
    total = 0
    found = False
    try:
        for process in psutil.process_iter(["name", "memory_info"]):
            try:
                name = str(process.info.get("name") or "").casefold()
                memory = process.info.get("memory_info")
                if ("ollama" in name or "llama" in name) and memory is not None:
                    total += int(memory.rss)
                    found = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        return None
    return total if found else None


def run_benchmark(iterations: int = 3, timeout_seconds: int = 60) -> dict[str, Any]:
    if not 1 <= iterations <= 20:
        raise ValueError("iterations must be between 1 and 20")
    if not 5 <= timeout_seconds <= 180:
        raise ValueError("timeout_seconds must be between 5 and 180")

    settings = db.get_settings()
    hardware = detect_hardware()
    limits = runtime_limits(settings, hardware)
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "ollama-local-chat-smoke-v1",
        "scope_note": "Bounded smoke prompt only; not a lesson-quality or full-workflow benchmark.",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpu_count": hardware.get("logical_cpu_count"),
            "total_ram_gb": hardware.get("total_ram_gb"),
            "available_ram_gb_before_gb": hardware.get("available_ram_gb"),
            "gpu_policy": hardware.get("gpu_policy", "disabled_by_default"),
            "runtime_limits": limits,
        },
        "configuration": {
            "model": str(settings.get("ollama_model", "")),
            "timeout_seconds": timeout_seconds,
            "iterations_requested": iterations,
            "num_ctx": min(limits["num_ctx"], 2048),
            "num_thread": limits["cpu_threads"],
            "num_predict": 96,
            "temperature": 0,
        },
        "ollama": {"available": False, "model_digest": None, "model_size_bytes": None},
        "samples": [],
        "process_memory": {
            "measurement": "sampled RSS of processes whose executable name contains ollama or llama",
            "baseline_rss_bytes": _ollama_related_rss_bytes(),
            "sampled_peak_rss_bytes": None,
        },
        "summary": {
            "success_count": 0,
            "failure_count": 0,
            "latency_median_ms": None,
            "latency_p95_ms": None,
        },
    }
    try:
        models = ollama.list_models()
        model_name = str(settings.get("ollama_model", ""))
        selected = next((item for item in models if item.get("name") == model_name), None)
        if selected is None:
            raise ollama.OllamaError("configured_model_not_found")
        report["ollama"] = {
            "available": True,
            "model_digest": selected.get("digest"),
            "model_size_bytes": selected.get("size"),
            "model_modified_at": selected.get("modified_at"),
        }
        latencies: list[float] = []
        peak_rss = report["process_memory"]["baseline_rss_bytes"]

        # One warm-up call is recorded separately and is not included in latency statistics.
        payload = {
            "model": model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You are a concise technical assistant. Return one short sentence."},
                {"role": "user", "content": "In one sentence, explain why a database index can speed up lookups and have a maintenance cost."},
            ],
            "options": {
                "temperature": 0,
                "num_ctx": min(limits["num_ctx"], 2048),
                "num_thread": limits["cpu_threads"],
                "num_predict": 96,
            },
        }
        for index in range(iterations + 1):
            started = time.perf_counter()
            try:
                response = ollama._request("/api/chat", payload, timeout=timeout_seconds)
                elapsed_ms = (time.perf_counter() - started) * 1000
                message = response.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str) or not content.strip():
                    raise ollama.OllamaError("empty_or_invalid_model_response")
                rss = _ollama_related_rss_bytes()
                if rss is not None:
                    peak_rss = rss if peak_rss is None else max(peak_rss, rss)
                if index == 0:
                    report["warmup"] = {"success": True, "latency_ms": round(elapsed_ms, 2)}
                else:
                    latencies.append(elapsed_ms)
                    report["samples"].append({"iteration": index, "success": True, "latency_ms": round(elapsed_ms, 2)})
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                if index == 0:
                    report["warmup"] = {"success": False, "error_kind": type(exc).__name__, "latency_ms": round(elapsed_ms, 2)}
                else:
                    report["samples"].append({"iteration": index, "success": False, "error_kind": type(exc).__name__, "latency_ms": round(elapsed_ms, 2)})
        report["process_memory"]["sampled_peak_rss_bytes"] = peak_rss
        report["summary"]["success_count"] = len(latencies)
        report["summary"]["failure_count"] = iterations - len(latencies)
        report["summary"]["latency_median_ms"] = round(statistics.median(latencies), 2) if latencies else None
        report["summary"]["latency_p95_ms"] = _percentile(latencies, 0.95)
    except Exception as exc:
        report["fatal_error_kind"] = type(exc).__name__
        report["fatal_error_code"] = str(exc)[:160]
        report["summary"]["failure_count"] = iterations
    report["environment"]["available_ram_gb_after_gb"] = detect_hardware().get("available_ram_gb")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3, help="Measured iterations (1–20; default 3)")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout in seconds (5–180)")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    args = parser.parse_args()
    try:
        report = run_benchmark(args.iterations, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["summary"]["success_count"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
