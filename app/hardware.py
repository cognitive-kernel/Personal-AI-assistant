from __future__ import annotations
import json
import math
import os
import re
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
        except Exception:
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
            "GPU discovery tries vendor tools, Windows generic performance counters, and Vulkan enumeration; unavailable metrics remain unknown.",
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
    if not isinstance(payload, dict):
        return []
    devices = []
    for index, (key, item) in enumerate(payload.items()):
        if not isinstance(item, dict):
            continue
        flat = {str(k).casefold().strip(): v for k, v in item.items()}
        def byte_value(*names):
            for name in names:
                value = flat.get(name)
                try:
                    return int(str(value).replace(",", "").strip())
                except (TypeError, ValueError):
                    continue
            return None
        def percent_value(*needles):
            for name, value in flat.items():
                if any(needle in name for needle in needles):
                    try:
                        number = float(str(value).replace("%", "").replace(",", "").strip())
                        return number if 0 <= number <= 100 else None
                    except (TypeError, ValueError):
                        continue
            return None
        total_bytes = byte_value("vram total memory (b)", "vram_total_memory_b")
        used_bytes = byte_value("vram total used memory (b)", "vram_total_used_memory_b")
        total_mb = round(total_bytes / (1024 ** 2), 1) if total_bytes is not None and total_bytes > 0 else None
        used_mb = round(used_bytes / (1024 ** 2), 1) if used_bytes is not None and used_bytes >= 0 else None
        util = percent_value("gpu use", "gpu utilization")
        if total_mb is None and used_mb is None and util is None:
            continue
        devices.append({
            "index": index,
            "name": str(flat.get("card series", flat.get("card model", flat.get("marketing name", key)))),
            "vendor": "AMD",
            "utilization_percent": util,
            "memory_used_mb": used_mb,
            "memory_total_mb": total_mb,
            "memory_free_mb": max(0, total_mb - used_mb) if total_mb is not None and used_mb is not None else None,
            "memory_percent": round(used_mb * 100 / total_mb, 1) if total_mb and used_mb is not None else None,
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
  $engine = @(Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine)
  $memory = @(Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory)
  foreach ($item in $engine) {
    $counters += [pscustomobject]@{ Path='GPU Engine Utilization Percentage'; InstanceName=$item.Name; CookedValue=$item.UtilizationPercentage }
  }
  foreach ($item in $memory) {
    $counters += [pscustomobject]@{ Path='GPU Adapter Memory Dedicated Usage'; InstanceName=$item.Name; CookedValue=$item.DedicatedUsage }
    $counters += [pscustomobject]@{ Path='GPU Adapter Memory Dedicated Limit'; InstanceName=$item.Name; CookedValue=$item.DedicatedLimit }
    $counters += [pscustomobject]@{ Path='GPU Adapter Memory Shared Usage'; InstanceName=$item.Name; CookedValue=$item.SharedUsage }
    $counters += [pscustomobject]@{ Path='GPU Adapter Memory Shared Limit'; InstanceName=$item.Name; CookedValue=$item.SharedLimit }
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
            len(adapters) == 1 or
            name.casefold().replace(" ", "_") in str(row.get("InstanceName", "")).casefold()
        )]
        engine = [float(row["CookedValue"]) for row in matching if "gpu engine" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        dedicated = [float(row["CookedValue"]) for row in matching if "dedicated usage" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        shared = [float(row["CookedValue"]) for row in matching if "shared usage" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float))]
        dedicated_limits = [float(row["CookedValue"]) for row in matching if "dedicated limit" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float)) and row["CookedValue"] > 0]
        shared_limits = [float(row["CookedValue"]) for row in matching if "shared limit" in str(row.get("Path", "")).casefold() and isinstance(row.get("CookedValue"), (int, float)) and row["CookedValue"] > 0]
        total_mb = None
        try:
            raw_ram = int(adapter.get("AdapterRAM") or 0)
            if raw_ram > 0:
                total_mb = round(raw_ram / (1024 ** 2), 1)
        except (TypeError, ValueError):
            pass
        used_mb = round(sum(dedicated) / (1024 ** 2), 1) if dedicated else None
        if dedicated_limits:
            total_mb = round(max(dedicated_limits) / (1024 ** 2), 1)
        shared_used_mb = round(sum(shared) / (1024 ** 2), 1) if shared else None
        shared_total_mb = round(max(shared_limits) / (1024 ** 2), 1) if shared_limits else None
        dedicated_free_mb = max(0, round(total_mb - used_mb, 1)) if total_mb is not None and used_mb is not None else None
        shared_free_mb = max(0, round(shared_total_mb - shared_used_mb, 1)) if shared_total_mb is not None and shared_used_mb is not None else None
        # WDDM's shared-memory limit is a maximum, not a promise that all of it is
        # currently free. Cap the usable shared portion by host RAM actually available.
        host_available_mb = None
        if psutil is not None:
            try:
                host_available = getattr(psutil.virtual_memory(), "available", None)
                if isinstance(host_available, (int, float)) and not isinstance(host_available, bool) and math.isfinite(host_available) and host_available >= 0:
                    host_available_mb = round(host_available / (1024 ** 2), 1)
            except Exception:
                host_available_mb = None
        usable_shared_free_mb = (
            min(shared_free_mb, host_available_mb)
            if shared_free_mb is not None and host_available_mb is not None
            else None
        )
        known_free_parts = [value for value in (dedicated_free_mb, usable_shared_free_mb) if value is not None]
        known_total_parts = [value for value in (total_mb, shared_total_mb) if value is not None]
        usable_free_mb = sum(known_free_parts) if known_free_parts else None
        usable_total_mb = sum(known_total_parts) if known_total_parts else None
        usable_memory_scope = (
            "dedicated+shared" if dedicated_free_mb is not None and usable_shared_free_mb is not None
            else "dedicated-only" if dedicated_free_mb is not None
            else "shared-only" if usable_shared_free_mb is not None
            else "unknown"
        )
        devices.append({
            "index": index, "name": name, "vendor": vendor,
            "utilization_percent": round(min(100.0, max(engine)), 1) if engine else None,
            "memory_used_mb": used_mb, "memory_total_mb": total_mb,
            "memory_free_mb": dedicated_free_mb,
            "shared_memory_used_mb": shared_used_mb,
            "shared_memory_total_mb": shared_total_mb,
            "shared_memory_free_mb": shared_free_mb,
            "usable_shared_memory_free_mb": usable_shared_free_mb,
            "usable_memory_total_mb": usable_total_mb,
            "usable_memory_free_mb": usable_free_mb,
            "usable_memory_scope": usable_memory_scope,
            "memory_percent": round(used_mb * 100 / total_mb, 1) if total_mb and used_mb is not None else None,
            "telemetry": "windows-performance-counters" if matching else "wmi-device-only",
        })
    return devices



def _detect_vulkan_gpus() -> list[dict[str, Any]]:
    """Vendor-neutral device enumeration fallback; Vulkan summary has no live utilization."""
    output = _run_gpu_command(["vulkaninfo", "--summary"], timeout=3.0)
    if not output:
        return []
    blocks = re.split(r"(?m)^\s*GPU\d+\s*:\s*$", output)
    devices = []
    for index, block in enumerate(blocks[1:]):
        name_match = re.search(r"(?m)^\s*deviceName\s*=\s*(.+?)\s*$", block)
        vendor_match = re.search(r"(?m)^\s*vendorID\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$", block)
        type_match = re.search(r"(?m)^\s*deviceType\s*=\s*(.+?)\s*$", block)
        if not name_match:
            continue
        vendor_id = vendor_match.group(1).lower() if vendor_match else ""
        vendor = {
            "0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel",
            "4318": "NVIDIA", "4098": "AMD", "32902": "Intel",
        }.get(vendor_id, "Unknown")
        devices.append({
            "index": index,
            "name": name_match.group(1).strip(),
            "vendor": vendor,
            "device_type": type_match.group(1).strip() if type_match else None,
            "utilization_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_free_mb": None,
            "memory_percent": None,
            "telemetry": "vulkan-device-enumeration-only",
        })
    return devices


def detect_gpu() -> dict[str, Any]:
    """Discover all GPUs across vendors; missing telemetry remains unknown, never zero."""
    detectors = (
        [_detect_nvidia_gpus, _detect_rocm_gpus, _detect_windows_gpus, _detect_vulkan_gpus]
        if os.name == "nt"
        else [_detect_nvidia_gpus, _detect_rocm_gpus, _detect_vulkan_gpus]
    )
    devices: list[dict[str, Any]] = []
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for detector in detectors:
        try:
            discovered = detector()
        except Exception:
            discovered = []
        for device in discovered:
            if not isinstance(device, dict) or not device.get("name"):
                continue
            vendor = str(device.get("vendor") or "Unknown").strip().casefold()
            name = " ".join(str(device["name"]).casefold().split())
            identity = (vendor, name)
            existing = identities.get(identity)
            if existing is None:
                normalized = dict(device)
                normalized["index"] = len(devices)
                devices.append(normalized)
                identities[identity] = normalized
                continue
            # Keep measured vendor-specific values; generic providers may only
            # fill gaps. This also avoids duplicate entries for the same adapter.
            for key, value in device.items():
                if key == "telemetry" and value:
                    current = str(existing.get(key) or "")
                    if str(value) not in current.split(","):
                        existing[key] = ",".join(filter(None, [current, str(value)]))
                elif existing.get(key) is None and value is not None:
                    existing[key] = value
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
            d.get("utilization_percent") is not None and d.get("memory_used_mb") is not None and d.get("memory_total_mb") is not None
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
        if isinstance(gpu.get("gpu_utilization_percent"), (int, float)) and gpu["gpu_utilization_percent"] >= gpu_limit:
            violations.append(f"GPU compute {gpu['gpu_utilization_percent']}% (limit {gpu_limit:.0f}%)")
        if isinstance(gpu.get("gpu_memory_percent"), (int, float)) and gpu["gpu_memory_percent"] >= gpu_limit:
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


def runtime_limits(
    settings: dict[str, Any],
    hardware: dict[str, Any] | None = None,
    *,
    maximum_context_tokens: int | None = None,
) -> dict[str, int]:
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
        # Missing/low available-memory telemetry keeps a conservative context.
        context = min(context, 2048)
    elif ram_budget < 8:
        context = min(context, 4096)
    else:
        # The model metadata, not a global constant, defines the hard ceiling.
        context = min(context, 1048576)
    if isinstance(maximum_context_tokens, int) and not isinstance(maximum_context_tokens, bool) and maximum_context_tokens >= 512:
        context = min(context, maximum_context_tokens)
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
