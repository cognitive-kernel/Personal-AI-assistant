#!/usr/bin/env python3
"""Collect a redacted, mostly read-only acceptance baseline on the target host.

By default this reads repository metadata and checks configured SQLite stores in
read-only mode. Pass --run-tests to execute the repository's pytest suite.
No database initialization, migration, repair, or model/network request is made.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import ipaddress
import json
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
    # The Ollama client rewrites the exact hostname "localhost" to 127.0.0.1
    # before opening a connection, so this value is safe without DNS resolution.
    # Other hostnames remain rejected by _ollama_endpoint_status.
    safe = bind_status == "loopback_literal" and ollama_status in {
        "loopback_literal", "localhost_normalized"
    }
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


def build_report(*, run_tests: bool = False, timeout_seconds: int = 1800) -> dict[str, Any]:
    storage = _storage_summary()
    network_boundary = _network_boundary_summary()
    tests = _run_tests(timeout_seconds) if run_tests else {"status": "not_run"}
    dirty = _git_value("status", "--porcelain")
    return {
        "report_version": 1,
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
            "packages": _package_versions(),
        },
        "storage": storage,
        "network_boundary": network_boundary,
        "tests": tests,
        "scope": {
            "storage_check_is_read_only": True,
            "database_initialization_performed": False,
            "database_migration_performed": False,
            "database_repair_performed": False,
            "live_model_evaluation_performed": False,
            "remote_execution_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-tests", action="store_true", help="run the full pytest suite")
    parser.add_argument(
        "--timeout-seconds", type=int, default=1800,
        help="pytest timeout when --run-tests is used (default: 1800)",
    )
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")

    report = build_report(run_tests=args.run_tests, timeout_seconds=args.timeout_seconds)
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
    return 0 if storage_ok and network_ok and tests_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
