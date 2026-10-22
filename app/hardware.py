from __future__ import annotations
import math
import os
import subprocess
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
    gpu = detect_gpu()
    return {
        "logical_cpu_count": logical_cpus,
        "total_ram_gb": total_ram_gb,
        "available_ram_gb": available_ram_gb,
        **gpu,
        "notes": [
            "CPU thread count is capped by the configurable CPU resource percentage.",
            "RAM budget is computed from currently available memory minus a safety reserve and capped by a configurable maximum; it is not a hard OS quota.",
            "RAM and GPU thresholds are monitored independently; external Ollama memory/compute cannot be hard-capped by this application.",
            "GPU telemetry is currently available for NVIDIA devices through nvidia-smi; unsupported or absent GPUs are reported as unknown.",
        ],
    }


def detect_gpu() -> dict[str, Any]:
    """Read NVIDIA utilization and VRAM using nvidia-smi without a shell."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError("nvidia-smi returned no GPU telemetry")
        rows = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                raise ValueError("unexpected nvidia-smi output")
            utilization, used_mb, total_mb = (int(part) for part in parts)
            if not (0 <= utilization <= 100 and 0 <= used_mb <= total_mb and total_mb > 0):
                raise ValueError("invalid GPU telemetry")
            rows.append({
                "utilization_percent": utilization,
                "memory_used_mb": used_mb,
                "memory_total_mb": total_mb,
                "memory_percent": round(used_mb * 100 / total_mb, 1),
            })
        if not rows:
            raise ValueError("no NVIDIA GPUs detected")
        return {
            "gpu_available": True, "gpu_count": len(rows),
            "gpu_utilization_percent": max(row["utilization_percent"] for row in rows),
            "gpu_memory_percent": max(row["memory_percent"] for row in rows),
            "gpu_devices": rows, "gpu_telemetry": "nvidia-smi",
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "gpu_available": False, "gpu_count": 0,
            "gpu_utilization_percent": None, "gpu_memory_percent": None,
            "gpu_devices": [], "gpu_telemetry": "unavailable",
        }

def _resource_limit(settings: dict[str, Any] | None, key: str) -> float:
    value = (settings or {}).get(key, 90)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 10 <= value <= 95:
        return 90.0
    return float(value)


def ensure_resource_headroom(settings: dict[str, Any] | None = None) -> None:
    """Refuse to start inference if any monitored resource reaches its own configured limit."""
    cpu_limit = _resource_limit(settings, "cpu_resource_limit_percent")
    ram_limit = _resource_limit(settings, "ram_resource_limit_percent")
    gpu_limit = _resource_limit(settings, "gpu_resource_limit_percent")
    gpu_enabled = (settings or {}).get("gpu_enabled", False) is True
    if psutil is None:
        raise RuntimeError("CPU/RAM telemetry is unavailable; refusing to start model inference safely.")
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    available_bytes = getattr(memory, "available", None)
    memory_percent = getattr(memory, "percent", None)
    if isinstance(available_bytes, bool) or not isinstance(available_bytes, (int, float)) or not math.isfinite(available_bytes) or available_bytes <= 0:
        raise RuntimeError("RAM availability telemetry is unavailable; refusing to start model inference safely.")
    if isinstance(memory_percent, bool) or not isinstance(memory_percent, (int, float)) or not 0 <= memory_percent <= 100:
        raise RuntimeError("RAM utilization telemetry is invalid; refusing to start model inference safely.")
    violations = []
    if cpu_percent >= cpu_limit:
        violations.append(f"CPU {cpu_percent:.1f}% (limit {cpu_limit:.0f}%)")
    if memory_percent >= ram_limit:
        violations.append(f"RAM {memory_percent:.1f}% (limit {ram_limit:.0f}%)")
    gpu = detect_gpu() if gpu_enabled else {"gpu_available": False}
    if gpu["gpu_available"]:
        if gpu["gpu_utilization_percent"] >= gpu_limit:
            violations.append(f"GPU compute {gpu['gpu_utilization_percent']}% (limit {gpu_limit:.0f}%)")
        if gpu["gpu_memory_percent"] >= gpu_limit:
            violations.append(f"GPU memory {gpu['gpu_memory_percent']:.1f}% (limit {gpu_limit:.0f}%)")
    if violations:
        raise RuntimeError("Resource usage is at or above configured limits: " + "; ".join(violations) + ".")


def dynamic_ram_budget_gb(
    settings: dict[str, Any] | None = None,
    hardware: dict[str, Any] | None = None,
) -> float | None:
    """Return a conservative RAM budget from available memory, never installed RAM alone."""
    settings = settings or {}
    hardware = hardware or detect_hardware()
    available = hardware.get("available_ram_gb")
    if isinstance(available, bool) or not isinstance(available, (int, float)) or not math.isfinite(available) or available < 0:
        return None
    maximum = settings.get("ram_budget_max_gb", 25)
    reserve = settings.get("ram_safety_reserve_gb", 4)
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or not 1 <= maximum <= 25:
        maximum = 25
    if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not math.isfinite(reserve) or not 1 <= reserve <= 8:
        reserve = 4
    return round(max(0.0, min(float(maximum), float(available) - float(reserve))), 2)


def runtime_limits(settings: dict[str, Any], hardware: dict[str, Any] | None = None) -> dict[str, int]:
    hardware = hardware or detect_hardware()
    cpu_count = max(1, int(hardware.get("logical_cpu_count") or 1))
    configured_threads = int(settings.get("ollama_num_thread", 0) or 0)
    cpu_limit = _resource_limit(settings, "cpu_resource_limit_percent")
    # Thread count is a best-effort CPU budget, not an OS-level CPU quota.
    thread_ceiling = max(1, int(cpu_count * cpu_limit / 100))
    requested_threads = configured_threads if configured_threads > 0 else thread_ceiling
    threads = min(thread_ceiling, requested_threads)
    ram_budget = dynamic_ram_budget_gb(settings, hardware)
    context = int(settings.get("ollama_num_ctx", 2048) or 2048)
    if ram_budget is None or ram_budget < 4:
        # Missing/low available-memory telemetry must not select a large context.
        context = min(context, 2048)
    elif ram_budget < 8:
        context = min(context, 4096)
    else:
        context = min(context, 8192)
    return {"cpu_threads": max(1, threads), "num_ctx": max(512, context)}


def model_keep_alive(
    purpose: str,
    hardware: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> str:
    """Keep only the interactive chat model warm when measured RAM headroom is ample.

    Long-lived learning/programming models can be substantially larger and are unloaded
    after each request to avoid accumulating several profile models in Ollama memory.
    """
    if purpose != "chat":
        return "0"
    hardware = hardware or detect_hardware()
    ram_budget = dynamic_ram_budget_gb(settings, hardware)
    if ram_budget is None:
        return "0"
    if ram_budget >= 12:
        return "5m"
    if ram_budget >= 4:
        return "1m"
    return "0"
