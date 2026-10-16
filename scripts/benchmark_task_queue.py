#!/usr/bin/env python3
"""Measure persistent task-queue throughput using disposable SQLite databases.

This is a reproducible diagnostic, not a production-capacity guarantee. It does
not touch the application's configured data stores and applies no pass/fail
throughput threshold because target hardware varies.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, task_manager


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def run_benchmark(
    *, task_count: int, producers: int, workers: int, settings_path: str, learning_path: str
) -> dict[str, Any]:
    """Run concurrent enqueues and concurrent claim/complete workers in an isolated store."""
    if isinstance(task_count, bool) or not isinstance(task_count, int) or not 1 <= task_count <= 100_000:
        raise ValueError("task_count must be an integer from 1 to 100000.")
    if isinstance(producers, bool) or not isinstance(producers, int) or not 1 <= producers <= 16:
        raise ValueError("producers must be an integer from 1 to 16.")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 16:
        raise ValueError("workers must be an integer from 1 to 16.")

    old_settings = db.defaults.settings_db_path
    old_learning = db.defaults.learning_db_path
    db.defaults.settings_db_path = settings_path
    db.defaults.learning_db_path = learning_path
    try:
        db.init_databases()
        task_manager.initialize_task_store()

        enqueue_started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=producers) as pool:
            ids = list(pool.map(
                lambda seq: task_manager.create_task(
                    "benchmark.noop", {"benchmark": True, "sequence": seq},
                    max_attempts=1,
                )["id"],
                range(task_count),
            ))
        enqueue_seconds = time.perf_counter() - enqueue_started

        lifecycle_started = time.perf_counter()
        completed = 0
        completed_lock = threading.Lock()
        stop_workers = threading.Event()
        deadline = time.monotonic() + max(60, task_count * 0.1)

        def consume(worker_number: int) -> None:
            nonlocal completed
            worker_id = f"benchmark-worker-{worker_number}"
            try:
                while not stop_workers.is_set():
                    with completed_lock:
                        if completed >= task_count:
                            return
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Queue benchmark exceeded its bounded execution deadline.")
                    task = task_manager.claim_next_task(worker_id, lease_seconds=60)
                    if task is None:
                        # Another worker may currently own the remaining tasks.
                        time.sleep(0.001)
                        continue
                    if task["task_type"] != "benchmark.noop" or task["payload"].get("benchmark") is not True:
                        raise RuntimeError("Benchmark encountered an unexpected task in its isolated store.")
                    task_manager.complete_task(task["id"], worker_id, {"ok": True})
                    with completed_lock:
                        completed += 1
            except BaseException:
                stop_workers.set()
                raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(consume, number) for number in range(workers)]
            for future in futures:
                future.result()
        lifecycle_seconds = time.perf_counter() - lifecycle_started

        with db.learning_connection() as connection:
            terminal_count = connection.execute(
                "SELECT COUNT(*) FROM task_queue WHERE status='succeeded'"
            ).fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        db_bytes = sum(
            Path(path).stat().st_size for path in (settings_path, learning_path)
            if Path(path).exists()
        )
        return {
            "report_schema_version": 1,
            "benchmark": "sqlite-task-queue-throughput",
            "git_commit": _git_commit(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sqlite_version": sqlite3.sqlite_version,
            "task_count_requested": task_count,
            "task_count_enqueued": len(ids),
            "task_count_succeeded": terminal_count,
            "producer_workers": producers,
            "consumer_workers": workers,
            "enqueue_seconds": round(enqueue_seconds, 6),
            "enqueue_tasks_per_second": round(task_count / max(enqueue_seconds, 1e-9), 2),
            "claim_and_complete_seconds": round(lifecycle_seconds, 6),
            "claim_and_complete_tasks_per_second": round(completed / max(lifecycle_seconds, 1e-9), 2),
            "database_bytes": db_bytes,
            "sqlite_integrity": integrity,
            "result": "passed" if len(ids) == task_count and terminal_count == task_count and integrity == "ok" else "failed",
            "interpretation": (
                "Diagnostic only; throughput is hardware/configuration-specific and does not "
                "prove crash safety, sustained-load capacity, or production readiness."
            ),
        }
    finally:
        db.defaults.settings_db_path = old_settings
        db.defaults.learning_db_path = old_learning


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=1000, help="Number of disposable tasks (1..100000).")
    parser.add_argument("--producers", type=int, default=4, help="Concurrent enqueue workers (1..16).")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent claim/complete workers (1..16).")
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()

    try:
        with tempfile.TemporaryDirectory(prefix="personal-ai-queue-bench-") as temp:
            root = Path(temp)
            report = run_benchmark(
                task_count=args.tasks,
                producers=args.producers,
                workers=args.workers,
                settings_path=str(root / "settings.db"),
                learning_path=str(root / "learning.db"),
            )
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))

    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
