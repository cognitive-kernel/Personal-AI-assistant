#!/usr/bin/env python3
"""Collect a redacted, mostly read-only acceptance baseline on the target host.

By default this reads repository metadata and checks configured SQLite stores in
read-only mode. Pass --run-tests to execute the repository's pytest suite.
No database initialization, migration, repair, or model/network request is made.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    tests_ok = report["tests"].get("status") in {"not_run", "passed"}
    return 0 if storage_ok and tests_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
