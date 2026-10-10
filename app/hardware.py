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
        "notes": ["CPU/context limits are configurable.", "GPU is not forced without verified backend support."],
    }

def runtime_limits(settings: dict[str, Any], hardware: dict[str, Any] | None = None) -> dict[str, int]:
    hardware = hardware or detect_hardware()
    cpu_count = max(1, int(hardware.get("logical_cpu_count") or 1))
    configured_threads = int(settings.get("ollama_num_thread", 0) or 0)
    threads = min(cpu_count, configured_threads) if configured_threads > 0 else cpu_count
    total_ram = hardware.get("total_ram_gb")
    context = int(settings.get("ollama_num_ctx", 2048) or 2048)
    if total_ram is not None and total_ram < 12:
        context = min(context, 2048)
    elif total_ram is not None and total_ram < 24:
        context = min(context, 4096)
    else:
        context = min(context, 8192)
    return {"cpu_threads": max(1, threads), "num_ctx": max(512, context)}
