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
    assert "localhost" not in json.dumps(report)


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
