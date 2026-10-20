#!/usr/bin/env python3
"""Collect a redacted, mostly read-only acceptance baseline on the target host.

By default this reads repository metadata and checks configured SQLite stores in
read-only mode. Opt-in flags run pytest and/or bounded disposable-queue and
loopback Ollama benchmarks. No database migration or repair is performed.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import ipaddress
import json
import math
import platform
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.hardware import detect_hardware

PACKAGE_NAMES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "psutil": "psutil",
    "python-dotenv": "python-dotenv",
    "pytest": "pytest",
}


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for label, distribution in PACKAGE_NAMES.items():
        try:
            versions[label] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = None
    return versions


def _effective_setting(key: str) -> tuple[Any, str]:
    """Read one setting without initializing or migrating its SQLite store."""
    from app.config import SETTING_DEFAULTS, defaults

    fallback = SETTING_DEFAULTS.get(key)
    database_path = Path(defaults.settings_db_path)
    if not database_path.is_file():
        return fallback, "default"
    connection = None
    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        row = connection.execute(
            "SELECT value_json FROM settings WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return fallback, "default"
        return json.loads(row[0]), "database"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None, "unavailable"
    finally:
        if connection is not None:
            connection.close()


def _ollama_endpoint_status(value: Any) -> str:
    """Classify the configured endpoint without making a network request or exposing it."""
    if not isinstance(value, str) or not value:
        return "unavailable"
    try:
        parsed = urlsplit(value.rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return "invalid_or_non_loopback"
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return "invalid_or_non_loopback"
        if parsed.hostname.lower() == "localhost":
            return "localhost_normalized"
        address = ipaddress.ip_address(parsed.hostname)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            return "invalid_or_non_loopback"
        return "loopback_literal" if address.is_loopback else "invalid_or_non_loopback"
    except (ValueError, TypeError):
        return "invalid_or_non_loopback"


def _network_boundary_summary() -> dict[str, Any]:
    """Check configured local-only network boundaries without probing the network."""
    from app.config import defaults

    try:
        bind_address = ipaddress.ip_address(str(defaults.host))
        if isinstance(bind_address, ipaddress.IPv6Address) and bind_address.ipv4_mapped is not None:
            bind_status = "hostname_or_invalid"
        elif bind_address.is_loopback:
            bind_status = "loopback_literal"
        else:
            bind_status = "non_loopback_literal"
    except ValueError:
        bind_status = "hostname_or_invalid"

    ollama_value, source = _effective_setting("ollama_base_url")
    ollama_status = _ollama_endpoint_status(ollama_value)
    # Do not treat a hostname as equivalent to a loopback IP literal. A local
    # hosts/DNS override can change where "localhost" resolves; this read-only
    # report deliberately does not resolve names or probe the network.
    safe = bind_status == "loopback_literal" and ollama_status == "loopback_literal"
    return {
        "status": "ok" if safe else "unsafe_or_unverified",
        "app_bind": bind_status,
        "ollama_endpoint": ollama_status,
        "ollama_setting_source": source,
        "raw_configuration_included": False,
        "network_probe_performed": False,
    }


def _storage_summary() -> dict[str, Any]:
    """Return only non-sensitive status fields; never include database paths."""
    try:
        from app.storage_health import check_storage_health

        result = check_storage_health()
    except Exception as exc:  # report class only; do not leak configuration or paths
        return {"status": "unavailable", "error_type": type(exc).__name__, "databases": []}
    databases = []
    for item in result.get("databases", []):
        databases.append({
            "name": item.get("name"),
            "status": item.get("status"),
            "schema_version": item.get("schema_version"),
            "expected_schema_version": item.get("expected_schema_version"),
            "integrity": item.get("integrity"),
            "foreign_key_violations": item.get("foreign_key_violations"),
            "migration_history_valid": item.get("migration_history_valid"),
            "schema_fingerprint_matches": item.get("schema_fingerprint_matches"),
        })
    return {
        "status": result.get("status", "unavailable"),
        "read_only": result.get("read_only") is True,
        "repair_performed": result.get("repair_performed") is True,
        "databases": databases,
    }


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in counts:
            try:
                counts[key] += int(suite.attrib.get(key, "0"))
            except ValueError:
                pass
    return counts


def _run_tests(timeout_seconds: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="personal-ai-baseline-") as temp_dir:
        junit_path = Path(temp_dir) / "pytest.xml"
        command = [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit_path}"]
        try:
            result = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True,
                timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout", "return_code": None,
                "timeout_seconds": timeout_seconds, "counts": None,
            }
        except OSError as exc:
            return {
                "status": "error", "return_code": None,
                "error_type": type(exc).__name__, "counts": None,
            }
        try:
            counts = _junit_counts(junit_path)
        except (OSError, ET.ParseError):
            counts = None
        return {
            "status": "passed" if result.returncode == 0 else "failed",
            "return_code": result.returncode,
            "counts": counts,
            # Deliberately omit raw output: tests or plugins may print local paths.
        }


def _run_performance_benchmark(task_count: int, min_queue_tps: float = 10.0, timeout_seconds: int = 300) -> dict[str, Any]:
    """Run the disposable SQLite queue benchmark and keep the report redacted."""
    if isinstance(task_count, bool) or not isinstance(task_count, int) or not 1 <= task_count <= 100000:
        raise ValueError("benchmark task count must be between 1 and 100000")
    if isinstance(min_queue_tps, bool) or not isinstance(min_queue_tps, (int, float)) or min_queue_tps <= 0:
        raise ValueError("minimum queue throughput must be positive")
    command = [
        sys.executable, str(ROOT / "scripts" / "benchmark_task_queue.py"),
        "--tasks", str(task_count), "--producers", "4", "--workers", "4",
    ]
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "timeout_seconds": timeout_seconds, "task_count": task_count}
    except OSError as exc:
        return {"status": "error", "error_type": type(exc).__name__, "task_count": task_count}
    try:
        report = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {
            "status": "invalid_report",
            "return_code": result.returncode,
            "task_count": task_count,
        }
    if not isinstance(report, dict):
        return {"status": "invalid_report", "return_code": result.returncode, "task_count": task_count}
    throughput = report.get("claim_and_complete_tasks_per_second")
    workload_passed = result.returncode == 0 and report.get("result") == "passed"
    threshold_passed = (
        isinstance(throughput, (int, float)) and not isinstance(throughput, bool)
        and throughput >= min_queue_tps
    )
    passed = workload_passed and threshold_passed
    return {
        "status": "passed" if passed else "below_threshold" if workload_passed and not threshold_passed else "failed",
        "return_code": result.returncode,
        "benchmark": report.get("benchmark"),
        "task_count_requested": report.get("task_count_requested"),
        "task_count_succeeded": report.get("task_count_succeeded"),
        "enqueue_tasks_per_second": report.get("enqueue_tasks_per_second"),
        "claim_and_complete_tasks_per_second": throughput,
        "minimum_claim_and_complete_tasks_per_second": float(min_queue_tps),
        "performance_threshold_passed": threshold_passed,
        "database_bytes": report.get("database_bytes"),
        "sqlite_integrity": report.get("sqlite_integrity"),
        "timed_out": False,
        "raw_output_included": False,
    }


def _run_local_model_benchmark(runs: int, min_tokens_per_second: float, max_latency_seconds: float, timeout_seconds: int = 900) -> dict[str, Any]:
    """Measure the configured loopback Ollama model without exposing generated text."""
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= 10:
        raise ValueError("model benchmark runs must be between 1 and 10")
    if not isinstance(min_tokens_per_second, (int, float)) or isinstance(min_tokens_per_second, bool) or not math.isfinite(min_tokens_per_second) or min_tokens_per_second <= 0:
        raise ValueError("minimum model throughput must be finite and positive")
    if not isinstance(max_latency_seconds, (int, float)) or isinstance(max_latency_seconds, bool) or not math.isfinite(max_latency_seconds) or max_latency_seconds <= 0:
        raise ValueError("maximum model latency must be finite and positive")
    command = [
        sys.executable, str(ROOT / "scripts" / "benchmark_local_model.py"),
        "--runs", str(runs), "--min-tokens-per-second", str(min_tokens_per_second),
        "--max-latency-seconds", str(max_latency_seconds),
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "timeout_seconds": timeout_seconds, "runs_requested": runs}
    except OSError as exc:
        return {"status": "error", "error_type": type(exc).__name__, "runs_requested": runs}
    try:
        report = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {"status": "invalid_report", "return_code": result.returncode, "runs_requested": runs}
    if not isinstance(report, dict):
        return {"status": "invalid_report", "return_code": result.returncode, "runs_requested": runs}
    return {
        "status": report.get("status") if report.get("status") != "passed" or result.returncode == 0 else "failed",
        "benchmark": report.get("benchmark"),
        "model": report.get("model"),
        "model_digest": report.get("model_digest"),
        "hardware_before": report.get("hardware_before"),
        "hardware_after": report.get("hardware_after"),
        "available_ram_delta_gb": report.get("available_ram_delta_gb"),
        "runs_requested": report.get("runs_requested", runs),
        "runs_completed": report.get("runs_completed", 0),
        "median_latency_seconds": report.get("median_latency_seconds"),
        "max_latency_seconds_observed": report.get("max_latency_seconds_observed"),
        "median_generation_tokens_per_second": report.get("median_generation_tokens_per_second"),
        "minimum_generation_tokens_per_second": report.get("minimum_generation_tokens_per_second", float(min_tokens_per_second)),
        "maximum_allowed_latency_seconds": report.get("maximum_allowed_latency_seconds", float(max_latency_seconds)),
        "performance_threshold_passed": report.get("performance_threshold_passed") is True and result.returncode == 0,
        "error_type": report.get("error_type"),
        "raw_output_included": False,
    }


def build_report(*, run_tests: bool = False, timeout_seconds: int = 1800, run_performance_benchmark: bool = False, benchmark_tasks: int = 1000, min_queue_tps: float = 10.0, run_model_benchmark: bool = False, model_benchmark_runs: int = 3, min_model_tps: float = 1.0, max_model_latency_seconds: float = 120.0) -> dict[str, Any]:
    storage = _storage_summary()
    network_boundary = _network_boundary_summary()
    tests = _run_tests(timeout_seconds) if run_tests else {"status": "not_run"}
    performance = _run_performance_benchmark(benchmark_tasks, min_queue_tps) if run_performance_benchmark else {"status": "not_run"}
    model_performance = _run_local_model_benchmark(model_benchmark_runs, min_model_tps, max_model_latency_seconds) if run_model_benchmark else {"status": "not_run"}
    dirty = _git_value("status", "--porcelain")
    return {
        "report_version": 4,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "repository": {
            "commit": _git_value("rev-parse", "HEAD"),
            "working_tree": "dirty" if dirty else "clean" if dirty == "" else "unknown",
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "system_release": platform.release(),
            "machine": platform.machine(),
            "hardware": detect_hardware(),
            "packages": _package_versions(),
        },
        "storage": storage,
        "network_boundary": network_boundary,
        "tests": tests,
        "performance_benchmark": performance,
        "local_model_performance_benchmark": model_performance,
        "scope": {
            "storage_check_is_read_only": True,
            "database_initialization_performed": False,
            "database_migration_performed": False,
            "database_repair_performed": False,
            "live_model_evaluation_performed": False,
            "performance_benchmark_performed": run_performance_benchmark,
            "local_model_performance_benchmark_performed": run_model_benchmark,
            "remote_execution_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-tests", action="store_true", help="run the full pytest suite")
    parser.add_argument("--run-performance-benchmark", action="store_true", help="benchmark disposable SQLite task queue on this host")
    parser.add_argument("--benchmark-tasks", type=int, default=1000, help="disposable benchmark task count (1..100000)")
    parser.add_argument("--min-queue-tps", type=float, default=10.0, help="minimum queue claim/complete throughput for acceptance (default: 10 tasks/s)")
    parser.add_argument("--run-model-benchmark", action="store_true", help="measure configured local Ollama generation latency and throughput")
    parser.add_argument("--model-benchmark-runs", type=int, default=3, help="measured local model generations (1..10; one warm-up added)")
    parser.add_argument("--min-model-tps", type=float, default=1.0, help="minimum median local model generation tokens/s")
    parser.add_argument("--max-model-latency-seconds", type=float, default=120.0, help="maximum median local model request latency")
    parser.add_argument(
        "--timeout-seconds", type=int, default=1800,
        help="pytest timeout when --run-tests is used (default: 1800)",
    )
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    if not 1 <= args.benchmark_tasks <= 100000:
        parser.error("--benchmark-tasks must be between 1 and 100000")
    if not math.isfinite(args.min_queue_tps) or args.min_queue_tps <= 0:
        parser.error("--min-queue-tps must be finite and positive")
    if not 1 <= args.model_benchmark_runs <= 10:
        parser.error("--model-benchmark-runs must be between 1 and 10")
    if not math.isfinite(args.min_model_tps) or args.min_model_tps <= 0:
        parser.error("--min-model-tps must be finite and positive")
    if not math.isfinite(args.max_model_latency_seconds) or args.max_model_latency_seconds <= 0:
        parser.error("--max-model-latency-seconds must be finite and positive")

    report = build_report(
        run_tests=args.run_tests,
        timeout_seconds=args.timeout_seconds,
        run_performance_benchmark=args.run_performance_benchmark,
        benchmark_tasks=args.benchmark_tasks,
        min_queue_tps=args.min_queue_tps,
        run_model_benchmark=args.run_model_benchmark,
        model_benchmark_runs=args.model_benchmark_runs,
        min_model_tps=args.min_model_tps,
        max_model_latency_seconds=args.max_model_latency_seconds,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"error_type": type(exc).__name__}), file=sys.stderr)
            return 2
    print(rendered, end="")
    storage_ok = report["storage"].get("status") == "ok"
    network_ok = report["network_boundary"].get("status") == "ok"
    tests_ok = report["tests"].get("status") in {"not_run", "passed"}
    performance_ok = report["performance_benchmark"].get("status") in ({"passed"} if args.run_performance_benchmark else {"not_run"})
    model_performance_ok = report["local_model_performance_benchmark"].get("status") in ({"passed"} if args.run_model_benchmark else {"not_run"})
    return 0 if storage_ok and network_ok and tests_ok and performance_ok and model_performance_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
