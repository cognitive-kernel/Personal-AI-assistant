from scripts.benchmark_learning_runtime import _percentile, run_benchmark


def test_percentile_handles_empty_single_and_interpolated_values():
    assert _percentile([], 0.95) is None
    assert _percentile([12.0], 0.95) == 12.0
    assert _percentile([10.0, 20.0, 30.0, 40.0], 0.95) == 38.5


def test_benchmark_bounds_iterations_and_timeout():
    import pytest

    with pytest.raises(ValueError, match="iterations"):
        run_benchmark(0, 60)
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_benchmark(1, 181)
