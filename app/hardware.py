from __future__ import annotations
import os
from typing import Any
try:
    import psutil
except ImportError:
    psutil = None

def detect_hardware() -> dict[str, Any]:
    logical_cpus = os.cpu_count() or 1
    if psutil:
        memory = psutil.virtual_memory()
        total_ram_gb = round(memory.total / (1024**3), 2)
        available_ram_gb = round(memory.available / (1024**3), 2)
    else:
        total_ram_gb = available_ram_gb = None
    return {
        "logical_cpu_count": logical_cpus,
        "total_ram_gb": total_ram_gb,
        "available_ram_gb": available_ram_gb,
        "gpu_policy": "disabled_by_default",
        "notes": [
            "Inference threads are capped at 90% of logical CPU capacity where the OS exposes at least two logical CPUs.",
            "Context limits reduce memory pressure but cannot guarantee a hard RAM ceiling for an external Ollama process.",
            "GPU is not forced without verified backend support.",
        ],
    }

def runtime_limits(settings: dict[str, Any], hardware: dict[str, Any] | None = None) -> dict[str, int]:
    hardware = hardware or detect_hardware()
    cpu_count = max(1, int(hardware.get("logical_cpu_count") or 1))
    configured_threads = int(settings.get("ollama_num_thread", 0) or 0)
    # Reserve at least 10% of logical CPU capacity for the operating system and other apps.
    # A single logical CPU cannot be split into a strict 90% thread budget; one thread is
    # the minimum Ollama accepts, so this remains a best-effort limit on single-core hosts.
    thread_ceiling = max(1, int(cpu_count * 0.9))
    requested_threads = configured_threads if configured_threads > 0 else thread_ceiling
    threads = min(thread_ceiling, requested_threads)
    total_ram = hardware.get("total_ram_gb")
    context = int(settings.get("ollama_num_ctx", 2048) or 2048)
    if total_ram is None or total_ram < 12:
        # Missing RAM telemetry must not select the largest context budget.
        context = min(context, 2048)
    elif total_ram < 24:
        context = min(context, 4096)
    else:
        context = min(context, 8192)
    return {"cpu_threads": max(1, threads), "num_ctx": max(512, context)}
