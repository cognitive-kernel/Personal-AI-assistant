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

def ensure_resource_headroom(limit_percent: float = 90.0) -> None:
    """Refuse to start new inference when system CPU or RAM is already at the safety threshold."""
    if psutil is None:
        raise RuntimeError("Resource telemetry is unavailable; refusing to start model inference safely.")
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory_percent = psutil.virtual_memory().percent
    if cpu_percent >= limit_percent or memory_percent >= limit_percent:
        raise RuntimeError(
            f"System resource usage is too high to start inference safely "
            f"(CPU {cpu_percent:.1f}%, RAM {memory_percent:.1f}%; limit {limit_percent:.0f}%)."
        )


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


def model_keep_alive(purpose: str, hardware: dict[str, Any] | None = None) -> str:
    """Keep only the interactive chat model warm when measured RAM headroom is ample.

    Long-lived learning/programming models can be substantially larger and are unloaded
    after each request to avoid accumulating several profile models in Ollama memory.
    """
    if purpose != "chat":
        return "0"
    hardware = hardware or detect_hardware()
    total_ram = hardware.get("total_ram_gb")
    available_ram = hardware.get("available_ram_gb")
    if (
        isinstance(total_ram, bool)
        or not isinstance(total_ram, (int, float))
        or isinstance(available_ram, bool)
        or not isinstance(available_ram, (int, float))
    ):
        return "0"
    if total_ram >= 16 and available_ram >= 12:
        return "5m"
    if total_ram >= 12 and available_ram >= 8:
        return "1m"
    return "0"
