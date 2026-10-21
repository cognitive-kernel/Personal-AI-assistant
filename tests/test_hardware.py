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

    assert limits == {"cpu_threads": 6, "num_ctx": 2048}



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
