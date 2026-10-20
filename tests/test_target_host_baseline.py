import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from app import cloud_resources, db


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_target_host_baseline.py"
SPEC = importlib.util.spec_from_file_location("target_host_baseline", SCRIPT)
baseline = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(baseline)


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )


def test_target_host_baseline_storage_summary_is_redacted_and_read_only(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    db.init_databases()
    cloud_resources.initialize_store()

    report = baseline.build_report()

    assert report["storage"]["status"] == "ok"
    assert report["storage"]["read_only"] is True
    assert report["storage"]["repair_performed"] is False
    assert report["tests"]["status"] == "not_run"
    assert report["scope"]["database_initialization_performed"] is False
    assert str(tmp_path) not in json.dumps(report)


def test_junit_counts_aggregate_child_suites(tmp_path):
    report = tmp_path / "pytest.xml"
    report.write_text(
        '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/>'
        '<testsuite tests="2" failures="0" errors="1" skipped="0"/></testsuites>',
        encoding="utf-8",
    )

    assert baseline._junit_counts(report) == {
        "tests": 5, "failures": 1, "errors": 1, "skipped": 1
    }


def test_target_host_baseline_reports_test_timeout_without_raw_output(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1, output="sensitive path")

    monkeypatch.setattr(baseline.subprocess, "run", timeout)
    result = baseline._run_tests(timeout_seconds=1)

    assert result == {
        "status": "timeout", "return_code": None,
        "timeout_seconds": 1, "counts": None,
    }
    assert "sensitive path" not in json.dumps(result)


def test_network_boundary_baseline_accepts_local_literals_without_exposing_urls(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(db.defaults, "host", "127.0.0.1")
    db.init_databases()
    db.update_settings({"ollama_base_url": "http://127.0.0.1:11434"})

    report = baseline.build_report()

    assert report["network_boundary"]["status"] == "ok"
    assert report["network_boundary"]["app_bind"] == "loopback_literal"
    assert report["network_boundary"]["ollama_endpoint"] in {
        "loopback_literal", "localhost_normalized"
    }
    assert report["network_boundary"]["raw_configuration_included"] is False
    assert "127.0.0.1" not in json.dumps(report["network_boundary"])


def test_network_boundary_baseline_does_not_assume_localhost_dns_is_loopback(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(db.defaults, "host", "127.0.0.1")
    db.init_databases()
    db.update_settings({"ollama_base_url": "http://localhost:11434"})

    report = baseline.build_report()

    assert report["network_boundary"]["status"] == "unsafe_or_unverified"
    assert report["network_boundary"]["app_bind"] == "loopback_literal"
    assert report["network_boundary"]["ollama_endpoint"] == "localhost_normalized"
    assert "http://localhost:11434" not in json.dumps(report)


def test_network_boundary_baseline_flags_non_loopback_bind_and_ollama_url(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(db.defaults, "host", "0.0.0.0")
    db.init_databases()
    db.update_settings({"ollama_base_url": "https://example.com:11434"})

    report = baseline.build_report()

    assert report["network_boundary"]["status"] == "unsafe_or_unverified"
    assert report["network_boundary"]["app_bind"] == "non_loopback_literal"
    assert report["network_boundary"]["ollama_endpoint"] == "invalid_or_non_loopback"
    assert "example.com" not in json.dumps(report)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434", "loopback_literal"),
        ("http://[::1]:11434", "loopback_literal"),
        ("http://localhost:11434", "localhost_normalized"),
        ("http://0.0.0.0:11434", "invalid_or_non_loopback"),
        ("http://[::ffff:127.0.0.1]:11434", "invalid_or_non_loopback"),
        ("https://example.com:11434", "invalid_or_non_loopback"),
        ("http://user:pass@127.0.0.1:11434", "invalid_or_non_loopback"),
        ("http://127.0.0.1:11434/api", "invalid_or_non_loopback"),
    ],
)
def test_ollama_endpoint_classifier_matches_local_only_policy(url, expected):
    assert baseline._ollama_endpoint_status(url) == expected


def test_target_host_performance_benchmark_reports_metrics_without_raw_output(monkeypatch):
    payload = {
        "result": "passed",
        "benchmark": "sqlite-task-queue-throughput",
        "task_count_requested": 100,
        "task_count_succeeded": 100,
        "enqueue_tasks_per_second": 250.0,
        "claim_and_complete_tasks_per_second": 120.0,
        "database_bytes": 4096,
        "sqlite_integrity": "ok",
    }
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["benchmark"], returncode=0, stdout=json.dumps(payload), stderr="private detail"
        ),
    )

    report = baseline._run_performance_benchmark(100)

    assert report["status"] == "passed"
    assert report["claim_and_complete_tasks_per_second"] == 120.0
    assert report["minimum_claim_and_complete_tasks_per_second"] == 10.0
    assert report["performance_threshold_passed"] is True
    assert report["sqlite_integrity"] == "ok"
    assert report["raw_output_included"] is False
    assert "private detail" not in json.dumps(report)


def test_target_host_performance_benchmark_fails_closed_on_invalid_report(monkeypatch):
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["benchmark"], returncode=0, stdout="not JSON", stderr=""
        ),
    )
    report = baseline._run_performance_benchmark(10)
    assert report["status"] == "invalid_report"


def test_target_host_performance_benchmark_fails_when_throughput_misses_floor(monkeypatch):
    payload = {
        "result": "passed",
        "benchmark": "sqlite-task-queue-throughput",
        "task_count_requested": 100,
        "task_count_succeeded": 100,
        "enqueue_tasks_per_second": 50.0,
        "claim_and_complete_tasks_per_second": 4.0,
        "database_bytes": 4096,
        "sqlite_integrity": "ok",
    }
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["benchmark"], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    report = baseline._run_performance_benchmark(100, min_queue_tps=10.0)
    assert report["status"] == "below_threshold"
    assert report["performance_threshold_passed"] is False


def test_target_host_model_benchmark_redacts_generated_text_and_reports_thresholds(monkeypatch):
    payload = {
        "status": "passed",
        "benchmark": "local-ollama-generation",
        "model": "test-model",
        "model_digest": "sha256:abc",
        "runs_requested": 3,
        "runs_completed": 3,
        "median_latency_seconds": 4.0,
        "max_latency_seconds_observed": 5.0,
        "median_generation_tokens_per_second": 12.5,
        "minimum_generation_tokens_per_second": 1.0,
        "maximum_allowed_latency_seconds": 120.0,
        "performance_threshold_passed": True,
        "secret_generated_text": "must not be copied",
    }
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["benchmark"], returncode=0, stdout=json.dumps(payload), stderr="private detail"
        ),
    )

    report = baseline._run_local_model_benchmark(3, 1.0, 120.0)

    assert report["status"] == "passed"
    assert report["median_generation_tokens_per_second"] == 12.5
    assert report["performance_threshold_passed"] is True
    assert report["raw_output_included"] is False
    assert "secret_generated_text" not in json.dumps(report)
    assert "must not be copied" not in json.dumps(report)
    assert "private detail" not in json.dumps(report)


def test_target_host_model_benchmark_fails_closed_when_model_is_unavailable(monkeypatch):
    payload = {
        "status": "model_not_found",
        "model": "configured-model",
        "runs_requested": 3,
        "runs_completed": 0,
        "performance_threshold_passed": False,
    }
    monkeypatch.setattr(
        baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["benchmark"], returncode=1, stdout=json.dumps(payload), stderr=""
        ),
    )
    report = baseline._run_local_model_benchmark(3, 1.0, 120.0)
    assert report["status"] == "model_not_found"
    assert report["performance_threshold_passed"] is False
