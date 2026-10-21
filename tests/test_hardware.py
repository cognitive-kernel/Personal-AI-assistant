import pytest
from types import SimpleNamespace

import app.hardware as hardware
from app.hardware import runtime_limits


def test_runtime_limits_clamp_threads_to_available_logical_cpus():
    limits = runtime_limits(
        {"ollama_num_thread": 32, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 4, "total_ram_gb": 32},
    )

    assert limits == {"cpu_threads": 3, "num_ctx": 8192}


def test_runtime_limits_respect_configured_thread_limit():
    limits = runtime_limits(
        {"ollama_num_thread": 2, "ollama_num_ctx": 4096},
        {"logical_cpu_count": 8, "total_ram_gb": 32},
    )

    assert limits["cpu_threads"] == 2


def test_runtime_limits_reduce_context_for_low_memory():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 2, "total_ram_gb": 8},
    )

    assert limits == {"cpu_threads": 1, "num_ctx": 2048}


def test_runtime_limits_apply_middle_memory_cap():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 6, "total_ram_gb": 16},
    )

    assert limits == {"cpu_threads": 5, "num_ctx": 4096}


def test_runtime_limits_enforce_minimum_context():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 128},
        {"logical_cpu_count": 6, "total_ram_gb": 32},
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
        virtual_memory=lambda: SimpleNamespace(percent=40.0),
    ))
    with pytest.raises(RuntimeError, match="CPU 90.0%"):
        hardware.ensure_resource_headroom()


def test_resource_headroom_rejects_ram_at_safety_threshold(monkeypatch):
    monkeypatch.setattr(hardware, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 35.0,
        virtual_memory=lambda: SimpleNamespace(percent=91.0),
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
        virtual_memory=lambda: SimpleNamespace(percent=65.0),
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
        virtual_memory=lambda: SimpleNamespace(percent=30.0),
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
        })


def test_detect_gpu_fails_closed_to_unknown_when_nvidia_smi_is_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi not installed")
    monkeypatch.setattr(hardware.subprocess, "run", missing)
    result = hardware.detect_gpu()
    assert result["gpu_available"] is False
    assert result["gpu_utilization_percent"] is None
