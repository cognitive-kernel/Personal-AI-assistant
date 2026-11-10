from __future__ import annotations
import json
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
    cpu_utilization_percent = None
    if psutil:
        try:
            cpu_utilization_percent = round(float(psutil.cpu_percent(interval=0.1)), 1)
        except (OSError, RuntimeError, TypeError, ValueError):
            cpu_utilization_percent = None
    gpu = detect_gpu()
    return {
        "logical_cpu_count": logical_cpus,
        "cpu_utilization_percent": cpu_utilization_percent,
        "cpu_available_percent": round(max(0.0, 100.0 - cpu_utilization_percent), 1) if cpu_utilization_percent is not None else None,
        "total_ram_gb": total_ram_gb,
        "available_ram_gb": available_ram_gb,
        **gpu,
        "notes": [
            "CPU thread count is capped by the configurable CPU resource percentage.",
            "RAM budget is computed from currently available memory minus a safety reserve and capped by a configurable maximum; it is not a hard OS quota.",
            "RAM and GPU thresholds are monitored independently; external Ollama memory/compute cannot be hard-capped by this application.",
            "GPU discovery tries vendor tools and Windows generic performance counters; unavailable metrics remain unknown.",
        ],
    }


def _run_gpu_command(command: list[str], timeout: float = 2.0) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _detect_nvidia_gpus() -> list[dict[str, Any]]:
    output = _run_gpu_command([
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not output:
        return []
    devices = []
    for index, line in enumerate(output.splitlines()):
        parts = [part.strip() for part in line.split(",", 3)]
        if len(parts) != 4:
            continue
        try:
            utilization, used_mb, total_mb = (int(part) for part in parts[1:])
        except ValueError:
            continue
        if not 0 <= utilization <= 100 or total_mb <= 0 or not 0 <= used_mb <= total_mb:
            continue
        devices.append({
            "index": index,
            "name": parts[0],
            "vendor": "NVIDIA",
            "utilization_percent": utilization,
            "memory_used_mb": used_mb,
            "memory_total_mb": total_mb,
            "memory_free_mb": total_mb - used_mb,
            "memory_percent": round(used_mb * 100 / total_mb, 1),
            "telemetry": "nvidia-smi",
        })
    return devices


def _detect_rocm_gpus() -> list[dict[str, Any]]:
    output = _run_gpu_command(["rocm-smi", "--showproductname", "--showuse", "--showmeminfo", "vram", "--json"])
    if not output:
        return []
    try:
        payload = json.loads(output)
    except (ValueError, TypeError):
        return []
    devices = []
    if not isinstance(payload, dict):
        return devices
    for index, (key, item) in enumerate(payload.items()):
        if not isinstance(item, dict):
            continue
        flat = {str(k).casefold(): v for k, v in item.items()}
        def number(*needles):
            for name, value in flat.items():
                if any(needle in name for needle in needles):
                    try:
                        return float(str(value).replace("%", "").replace(" MB", "").replace(",", "").strip())
                    except (TypeError, ValueError):
                        continue
            return None
        total = number("vram total", "memory total")
        used = number("vram used", "memory used")
        util = number("gpu use", "gpu utilization")
        if total is None and used is None and util is None:
            continue
        devices.append({
            "index": index, "name": str(flat.get("card series", flat.get("product name", key))),
            "vendor": "AMD", "utilization_percent": util,
            "memory_used_mb": used, "memory_total_mb": total,
            "memory_free_mb": max(0, total - used) if total is not None and used is not None else None,
            "memory_percent": round(used * 100 / total, 1) if total and used is not None else None,
            "telemetry": "rocm-smi",
        })
    return devices


def _detect_windows_gpus() -> list[dict[str, Any]]:
    """Use Windows WMI plus generic GPU performance counters (works across vendors)."""
    if os.name != "nt":
        return []
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$adapters = @(Get-CimInstance Win32_VideoController | ForEach-Object {
  [pscustomobject]@{ Name=$_.Name; PNPDeviceID=$_.PNPDeviceID; AdapterRAM=$_.AdapterRAM }
})
$counters = @()
try {
  $counters = (Get-Counter -Counter @(
    '\GPU Engine(*)\Utilization Percentage',
    '\GPU Adapter Memory(*)\Dedicated Usage',
    '\GPU Adapter Memory(*)\Shared Usage'
  ) -SampleInterval 1 -MaxSamples 1).CounterSamples | ForEach-Object {
    [pscustomobject]@{ Path=$_.Path; InstanceName=$_.InstanceName; CookedValue=$_.CookedValue }
  }
} catch {}
[pscustomobject]@{Adapters=$adapters; Counters=$counters} | ConvertTo-Json -Depth 5 -Compress
"""
    output = _run_gpu_command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=4.0)
    if not output:
        return []
    try:
        payload = json.loads(output)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    adapters = payload.get("Adapters") or []
    counters = payload.get("Counters") or []
    if isinstance(adapters, dict):
        adapters = [adapters]
    if isinstance(counters, dict):
        counters = [counters]
    devices = []
    for index, adapter in enumerate(adapters):
        if not isinstance(adapter, dict):
            continue
        name = str(adapter.get("Name") or "Unknown GPU")
        pnp = str(adapter.get("PNPDeviceID") or "")
        vendor = "Intel" if "VEN_8086" in pnp.upper() or "INTEL" in name.upper() else (
            "AMD" if "VEN_1002" in pnp.upper() or "AMD" in name.upper() else (
                "NVIDIA" if "VEN_10DE" in pnp.upper() or "NVIDIA" in name.upper() else "Unknown"
            )
        )
        matching = [row for row in counters if isinstance(row, dict) and (
            str(index) in str(row.get("InstanceName", "")) or
            name.casefold().replace(" ", "_") in str(row.get("InstanceName", "")).casefold()
        )]
        engine = [float(row["CookedValue"]) for row in matching if "gpu engine" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        dedicated = [float(row["CookedValue"]) for row in matching if "dedicated usage" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        shared = [float(row["CookedValue"]) for row in matching if "shared usage" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        total_mb = None
        try:
            raw_ram = int(adapter.get("AdapterRAM") or 0)
            if raw_ram > 0:
                total_mb = round(raw_ram / (1024 ** 2), 1)
        except (TypeError, ValueError):
            pass
        used_mb = round(sum(dedicated) / (1024 ** 2), 1) if dedicated else None
        devices.append({
            "index": index, "name": name, "vendor": vendor,
            "utilization_percent": round(min(100.0, max(engine)), 1) if engine else None,
            "memory_used_mb": used_mb, "memory_total_mb": total_mb,
            "memory_free_mb": max(0, round(total_mb - used_mb, 1)) if total_mb is not None and used_mb is not None else None,
            "shared_memory_used_mb": round(sum(shared) / (1024 ** 2), 1) if shared else None,
            "memory_percent": round(used_mb * 100 / total_mb, 1) if total_mb and used_mb is not None else None,
            "telemetry": "windows-performance-counters" if matching else "wmi-device-only",
        })
    return devices


def detect_gpu() -> dict[str, Any]:
    """Discover GPUs across vendors; missing telemetry remains unknown, never zero."""
    providers = (
        ("nvidia-smi", _detect_nvidia_gpus),
        ("rocm-smi", _detect_rocm_gpus),
        ("windows-performance-counters", _detect_windows_gpus),
    )
    devices: list[dict[str, Any]] = []
    for provider, detector in providers:
        try:
            devices = detector()
        except Exception:
            devices = []
        if devices:
            break
    measurable_util = [d["utilization_percent"] for d in devices if isinstance(d.get("utilization_percent"), (int, float))]
    measurable_memory = [d["memory_percent"] for d in devices if isinstance(d.get("memory_percent"), (int, float))]
    telemetry_names = sorted({str(d.get("telemetry")) for d in devices if d.get("telemetry")})
    telemetry = ",".join(telemetry_names) if telemetry_names else "device-enumeration-only"
    return {
        "gpu_available": bool(devices),
        "gpu_count": len(devices),
        "gpu_utilization_percent": round(max(measurable_util), 1) if measurable_util else None,
        "gpu_memory_percent": round(max(measurable_memory), 1) if measurable_memory else None,
        "gpu_devices": devices,
        "gpu_telemetry": telemetry if devices else "unavailable",
        "gpu_telemetry_complete": bool(devices) and all(
            d.get("utilization_percent") is not None and d.get("memory_total_mb") is not None
            for d in devices
        ),
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
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not 1 <= maximum <= 25:
        maximum = 25
    if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not 1 <= reserve <= 8:
        reserve = 4
    # The configured ceiling includes the safety reserve; only the remainder is model budget.
    return round(max(0.0, min(float(maximum), float(available)) - float(reserve)), 2)


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
    if purpose not in {"chat", "learning", "programming"}:
        return "0"
    settings = settings or {}
    configured = settings.get(f"{purpose}_model_keep_alive", "auto" if purpose == "chat" else "0")
    allowed = {"auto", "0", "1m", "5m", "10m", "30m"}
    if isinstance(configured, str) and configured in allowed and configured != "auto":
        return configured
    if not isinstance(configured, str) or configured != "auto" or purpose != "chat":
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
