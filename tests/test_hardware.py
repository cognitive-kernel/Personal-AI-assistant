from app.hardware import runtime_limits


def test_runtime_limits_clamp_threads_to_available_logical_cpus():
    limits = runtime_limits(
        {"ollama_num_thread": 32, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 4, "total_ram_gb": 32},
    )

    assert limits == {"cpu_threads": 4, "num_ctx": 8192}


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

    assert limits == {"cpu_threads": 2, "num_ctx": 2048}


def test_runtime_limits_apply_middle_memory_cap():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 8192},
        {"logical_cpu_count": 6, "total_ram_gb": 16},
    )

    assert limits == {"cpu_threads": 6, "num_ctx": 4096}


def test_runtime_limits_enforce_minimum_context():
    limits = runtime_limits(
        {"ollama_num_thread": 0, "ollama_num_ctx": 128},
        {"logical_cpu_count": 6, "total_ram_gb": 32},
    )

    assert limits == {"cpu_threads": 6, "num_ctx": 512}
