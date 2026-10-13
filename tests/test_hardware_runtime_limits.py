import pytest

from app.hardware import runtime_limits


@pytest.mark.parametrize(
    ("hardware", "expected_context"),
    [
        ({"logical_cpu_count": 4, "total_ram_gb": None}, 2048),
        ({"logical_cpu_count": 4}, 2048),
        ({"logical_cpu_count": 4, "total_ram_gb": 8}, 2048),
        ({"logical_cpu_count": 4, "total_ram_gb": 16}, 4096),
        ({"logical_cpu_count": 4, "total_ram_gb": 32}, 8192),
    ],
)
def test_runtime_context_budget_is_conservative_without_ram_telemetry(
    hardware, expected_context
):
    limits = runtime_limits(
        {"ollama_num_thread": 99, "ollama_num_ctx": 8192},
        hardware,
    )

    assert limits == {"cpu_threads": 4, "num_ctx": expected_context}
