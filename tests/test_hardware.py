import pytest
from types import SimpleNamespace

import app.hardware as hardware
from app.hardware import runtime_limits


def test_runtime_limits_clamp_threads_to_available_logical_cpus():
    limits = runtime_limits(
        {"ollama_num_thread": 32, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 4, "total_ram_gb": 32, "available_ram_gb": 25},
    )

    assert limits == {"cpu_threads": 3, "num_ctx": 8192}


def test_runtime_limits_respect_configured_thread_limit():
    limits = runtime_limits(
        {"ollama_num_thread": 2, "ollama_num_ctx": 4096},
        {"logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 25},
    )

    assert limits["cpu_threads"] == 2


def test_runtime_limits_reduce_context_for_low_memory():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 2, "total_ram_gb": 8, "available_ram_gb": 4},
    )

    assert limits == {"cpu_threads": 1, "num_ctx": 2048}


def test_runtime_limits_apply_middle_memory_cap():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 6, "total_ram_gb": 16, "available_ram_gb": 10},
    )

    assert limits == {"cpu_threads": 5, "num_ctx": 4096}


def test_runtime_limits_enforce_minimum_context():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 128},
        {"logical_cpu_count": 6, "total_ram_gb": 32, "available_ram_gb": 25},
    )

    assert limits == {"cpu_threads": 5, "num_ctx": 512}


def test_runtime_limits_use_conservative_context_when_ram_is_unknown():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 6, "total_ram_gb": None},
    )

    assert limits == {"cpu_threads": 5, "num_ctx": 2048}


def test_runtime_limits_use_conservative_context_when_ram_field_is_missing():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 6},
    )

    assert limits == {"cpu_threads": 5, "num_ctx": 2048}



def test_resource_headroom_rejects_cpu_at_safety_threshold(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 90.0,
        virtual_memory=lambda: SimpleNamespace(percent=40.0, available=8 * 1024**3),
    ))
    with pytest.raises(RuntimeError, match="CPU 90.0%"):
        hardware.ensure_resource_headroom()


def test_resource_headroom_rejects_ram_at_safety_threshold(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 35.0,
        virtual_memory=lambda: SimpleNamespace(percent=91.0, available=8 * 1024**3),
    ))
    with pytest.raises(RuntimeError, match="RAM 91.0%"):
        hardware.ensure_resource_headroom()



@pytest.mark.parametrize(
    ("purpose", "hardware_info", "expected"),
    [
        ("chat", {"total_ram_gb": 32, "available_ram_gb": 25}, "5m"),
        ("chat", {"total_ram_gb": 16, "available_ram_gb": 9}, "1m"),
        ("chat", {"total_ram_gb": 32, "available_ram_gb": 7}, "0"),
        ("chat", {"total_ram_gb": None, "available_ram_gb": None}, "0"),
        ("learning", {"total_ram_gb": 32, "available_ram_gb": 25}, "0"),
        ("programming", {"total_ram_gb": 32, "available_ram_gb": 25}, "0"),
    ],
)
def test_model_keep_alive_is_ram_aware_and_profile_safe(purpose, hardware_info, expected):
    assert hardware.model_keep_alive(purpose, hardware_info) == expected


@pytest.mark.parametrize(
    ("purpose", "setting", "expected"),
    [
        ("chat", "0", "0"),
        ("chat", "10m", "10m"),
        ("learning", "5m", "5m"),
        ("programming", "30m", "30m"),
        ("learning", "auto", "0"),
        ("programming", "auto", "0"),
    ],
)
def test_model_keep_alive_respects_explicit_profile_setting(purpose, setting, expected):
    assert hardware.model_keep_alive(
        purpose,
        {"available_ram_gb": 25},
        {f"{purpose}_model_keep_alive": setting},
    ) == expected


def test_chat_model_keep_alive_auto_uses_dynamic_ram_budget():
    assert hardware.model_keep_alive(
        "chat", {"available_ram_gb": 25}, {"chat_model_keep_alive": "auto", "ram_safety_reserve_gb": 4}
    ) == "5m"
    assert hardware.model_keep_alive(
        "chat", {"available_ram_gb": 6}, {"chat_model_keep_alive": "auto", "ram_safety_reserve_gb": 4}
    ) == "0"



def test_runtime_limits_use_configured_cpu_percentage():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 4096, "cpu_resource_limit_percent": 50},
        {"logical_cpu_count": 8, "total_ram_gb": 32},
    )
    assert limits["cpu_threads"] == 4


def test_resource_headroom_uses_independent_cpu_and_ram_limits(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu", lambda: {"gpu_available": False})
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 75.0,
        virtual_memory=lambda: SimpleNamespace(percent=65.0, available=8 * 1024**3),
    ))
    with pytest.raises(RuntimeError, match="CPU 75.0%"):
        hardware.ensure_resource_headroom({
            "cpu_resource_limit_percent": 70,
            "ram_resource_limit_percent": 80,
            "gpu_resource_limit_percent": 90,
        })


def test_resource_headroom_checks_gpu_compute_and_vram_independently(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 20.0,
        virtual_memory=lambda: SimpleNamespace(percent=30.0, available=8 * 1024**3),
    ))
    monkeypatch.setattr(hardware, "detect_gpu", lambda: {
        "gpu_available": True,
        "gpu_utilization_percent": 40,
        "gpu_memory_percent": 92.0,
    })
    with pytest.raises(RuntimeError, match="GPU memory 92.0%"):
        hardware.ensure_resource_headroom({
            "cpu_resource_limit_percent": 90,
            "ram_resource_limit_percent": 90,
            "gpu_resource_limit_percent": 85,
            "gpu_enabled": True,
        })


def test_detect_gpu_fails_closed_to_unknown_when_nvidia_smi_is_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi not installed")
    monkeypatch.setattr(hardware.subprocess, "run", missing)
    result = hardware.detect_gpu()
    assert result["gpu_available"] is False
    assert result["gpu_utilization_percent"] is None


def test_gpu_resource_guard_is_skipped_when_gpu_is_disabled(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 20.0,
        virtual_memory=lambda: SimpleNamespace(percent=30.0, available=8 * 1024**3),
    ))
    def unexpected_gpu_probe():
        raise AssertionError("GPU telemetry should not be queried while GPU is disabled")
    monkeypatch.setattr(hardware, "detect_gpu", unexpected_gpu_probe)
    hardware.ensure_resource_headroom({"gpu_enabled": False, "cpu_resource_limit_percent": 90, "ram_resource_limit_percent": 90})


@pytest.mark.parametrize(
    ("available", "maximum", "reserve", "expected"),
    [
        (25, 25, 4, 21),
        (12, 25, 4, 8),
        (6, 25, 4, 2),
        (32, 20, 5, 15),
        (4, 25, 4, 0),
        (0, 25, 4, 0),
        (None, 25, 4, None),
        (float("nan"), 25, 4, None),
    ],
)
def test_dynamic_ram_budget_uses_available_memory_and_safety_reserve(available, maximum, reserve, expected):
    assert hardware.dynamic_ram_budget_gb(
        {"ram_budget_max_gb": maximum, "ram_safety_reserve_gb": reserve},
        {"available_ram_gb": available},
    ) == expected


def test_resource_headroom_fails_closed_when_available_ram_telemetry_is_missing(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 20.0,
        virtual_memory=lambda: SimpleNamespace(percent=30.0),
    ))
    with pytest.raises(RuntimeError, match="RAM availability telemetry is unavailable"):
        hardware.ensure_resource_headroom()


def test_dynamic_ram_budget_rejects_non_finite_telemetry():
    assert hardware.dynamic_ram_budget_gb({}, {"available_ram_gb": float("nan")}) is None


def test_resource_headroom_fails_closed_when_psutil_is_unavailable(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", None)
    with pytest.raises(RuntimeError, match="CPU/RAM telemetry is unavailable"):
        hardware.ensure_resource_headroom()

def test_ram_ceiling_subtracts_safety_reserve_before_context_selection():
    limits = hardware.runtime_limits(
        {
            "ollama_num_ctx": 8192,
            "ollama_num_thread": 0,
            "ram_budget_max_gb": 6,
            "ram_safety_reserve_gb": 4,
        },
        {"logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 24},
    )
    assert limits["num_ctx"] == 2048



def test_detect_gpu_uses_generic_windows_telemetry_for_single_adapter(monkeypatch):
    import json
    monkeypatch.setattr(hardware.os, "name", "nt")
    payload = {
        "Adapters": [{"Name": "Intel(R) UHD Graphics 730", "PNPDeviceID": "PCI\\VEN_8086&DEV_4692", "AdapterRAM": 2147483648}],
        "Counters": [
            {"Path": r"\\GPU Engine(*)\\Utilization Percentage", "InstanceName": "pid_1_luid_0x00000000_0x0000_phys_0_eng_0_engtype_3D", "CookedValue": 18.5},
            {"Path": r"\\GPU Adapter Memory(*)\\Dedicated Usage", "InstanceName": "luid_0x00000000_0x0000_phys_0", "CookedValue": 268435456},
            {"Path": r"\\GPU Adapter Memory(*)\\Dedicated Limit", "InstanceName": "luid_0x00000000_0x0000_phys_0", "CookedValue": 2147483648},
            {"Path": r"\\GPU Adapter Memory(*)\\Shared Usage", "InstanceName": "luid_0x00000000_0x0000_phys_0", "CookedValue": 536870912},
            {"Path": r"\\GPU Adapter Memory(*)\\Shared Limit", "InstanceName": "luid_0x00000000_0x0000_phys_0", "CookedValue": 4294967296},
        ],
    }
    monkeypatch.setattr(hardware, "_run_gpu_command", lambda *args, **kwargs: json.dumps(payload))
    result = hardware._detect_windows_gpus()
    assert len(result) == 1
    assert result[0]["vendor"] == "Intel"
    assert result[0]["utilization_percent"] == 18.5
    assert result[0]["memory_used_mb"] == 256
    assert result[0]["shared_memory_used_mb"] == 512
    assert result[0]["shared_memory_total_mb"] == 4096
    assert result[0]["usable_memory_free_mb"] == 5376


def test_detect_gpu_keeps_discovered_device_when_metrics_are_unknown(monkeypatch):
    monkeypatch.setattr(hardware, "_detect_nvidia_gpus", lambda: [])
    monkeypatch.setattr(hardware, "_detect_rocm_gpus", lambda: [])
    monkeypatch.setattr(hardware, "_detect_windows_gpus", lambda: [{
        "index": 0, "name": "Future GPU", "vendor": "Unknown",
        "utilization_percent": None, "memory_used_mb": None,
        "memory_total_mb": None, "memory_free_mb": None,
        "memory_percent": None, "telemetry": "device-enumeration-only",
    }])
    result = hardware.detect_gpu()
    assert result["gpu_available"] is True
    assert result["gpu_count"] == 1
    assert result["gpu_utilization_percent"] is None
    assert result["gpu_memory_percent"] is None
    assert result["gpu_telemetry_complete"] is False


def test_resource_headroom_does_not_compare_unknown_gpu_metrics(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 20.0,
        virtual_memory=lambda: SimpleNamespace(percent=30.0, available=8 * 1024**3),
    ))
    monkeypatch.setattr(hardware, "detect_gpu", lambda: {
        "gpu_available": True,
        "gpu_utilization_percent": None,
        "gpu_memory_percent": None,
    })
    hardware.ensure_resource_headroom({
        "gpu_enabled": True,
        "cpu_resource_limit_percent": 90,
        "ram_resource_limit_percent": 90,
        "gpu_resource_limit_percent": 90,
    })


def test_detect_hardware_exposes_cpu_available_capacity(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 37.5,
        virtual_memory=lambda: SimpleNamespace(total=16 * 1024**3, available=9 * 1024**3),
    ))
    monkeypatch.setattr(hardware, "detect_gpu", lambda: {
        "gpu_available": False, "gpu_count": 0, "gpu_utilization_percent": None,
        "gpu_memory_percent": None, "gpu_devices": [], "gpu_telemetry": "unavailable",
    })
    result = hardware.detect_hardware()
    assert result["cpu_utilization_percent"] == 37.5
    assert result["cpu_available_percent"] == 62.5


def test_rocm_vram_byte_counters_are_converted_to_mebibytes(monkeypatch):
    import json
    payload = {
        "card0": {
            "Card series": "AMD Radeon RX 7900 XTX",
            "VRAM Total Memory (B)": "25753026560",
            "VRAM Total Used Memory (B)": "1073741824",
            "GPU use (%)": "42",
        }
    }
    monkeypatch.setattr(hardware, "_run_gpu_command", lambda *args, **kwargs: json.dumps(payload))
    devices = hardware._detect_rocm_gpus()
    assert len(devices) == 1
    assert devices[0]["vendor"] == "AMD"
    assert devices[0]["memory_total_mb"] == round(25753026560 / 1024**2, 1)
    assert devices[0]["memory_used_mb"] == 1024
    assert devices[0]["utilization_percent"] == 42
