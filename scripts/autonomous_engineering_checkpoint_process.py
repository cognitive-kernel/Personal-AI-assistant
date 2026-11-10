from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import psutil


def process_start_time(pid: int) -> float:
    """Return the OS creation time used to distinguish a process from a reused PID."""
    try:
        value = float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        raise RuntimeError(f"Cannot verify OS process creation time for PID {pid}.") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"OS returned an invalid process creation time for PID {pid}.")
    return value


def _same_path(left: str, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except (OSError, TypeError, ValueError):
        return False


def _owns_watcher_command(
    command: list[str],
    *,
    script_path: Path,
    run_id: str,
    task: int,
    base_sha: str,
) -> bool:
    script_index = next(
        (index for index, value in enumerate(command) if _same_path(value, script_path)),
        None,
    )
    if script_index is None:
        return False
    arguments = command[script_index + 1 :]
    expected = {
        "--run-id": run_id,
        "--task": str(task),
        "--base-sha": base_sha,
    }
    if not arguments or arguments[0] != "watch":
        return False
    for flag, value in expected.items():
        try:
            index = arguments.index(flag)
        except ValueError:
            return False
        if index + 1 >= len(arguments) or arguments[index + 1] != value:
            return False
    return True


def stop_owned_watcher(
    metadata: Any,
    *,
    script_path: Path,
    timeout_seconds: float = 5.0,
) -> None:
    """Stop only the exact process recorded by a checkpoint; fail closed if ownership is ambiguous."""
    if not isinstance(metadata, dict):
        return
    pid = metadata.get("watcher_pid")
    ready = metadata.get("watcher_ready") is True
    if pid is None:
        if ready:
            raise RuntimeError("Checkpoint claims watcher readiness but has no watcher PID.")
        return
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("Checkpoint watcher PID is invalid; refusing recovery or cleanup.")

    run_id = metadata.get("run_id")
    task = metadata.get("task")
    base_sha = metadata.get("base_sha")
    if (
        not isinstance(run_id, str) or not run_id
        or isinstance(task, bool) or not isinstance(task, int) or task < 1
        or not isinstance(base_sha, str) or len(base_sha) != 40
    ):
        raise RuntimeError("Checkpoint watcher ownership metadata is incomplete.")

    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied as exc:
        raise RuntimeError(
            f"Cannot inspect checkpoint watcher PID {pid}; recovery is blocked to prevent concurrent writes."
        ) from exc

    try:
        actual_start = float(process.create_time())
        command = process.cmdline()
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied as exc:
        raise RuntimeError(
            f"Cannot verify checkpoint watcher PID {pid}; recovery is blocked to prevent concurrent writes."
        ) from exc

    expected_start = metadata.get("watcher_started_at")
    if (
        isinstance(expected_start, bool)
        or not isinstance(expected_start, (int, float))
        or not math.isfinite(expected_start)
        or expected_start <= 0
    ):
        if _owns_watcher_command(
            command,
            script_path=script_path,
            run_id=run_id,
            task=task,
            base_sha=base_sha,
        ):
            raise RuntimeError(
                "The active checkpoint watcher uses legacy metadata without a process-start identity; "
                "refusing to terminate or restore over it."
            )
        return

    # A different creation time proves that the stored PID has been reused.
    if abs(actual_start - float(expected_start)) > 0.02:
        return
    if not _owns_watcher_command(
        command,
        script_path=script_path,
        run_id=run_id,
        task=task,
        base_sha=base_sha,
    ):
        raise RuntimeError(
            f"Checkpoint PID {pid} is alive but its command line does not match the recorded run/task; "
            "refusing recovery or cleanup."
        )

    try:
        process.terminate()
        try:
            process.wait(timeout=max(0.1, timeout_seconds))
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=max(0.1, timeout_seconds))
    except psutil.NoSuchProcess:
        return
    except (psutil.AccessDenied, psutil.TimeoutExpired, OSError) as exc:
        raise RuntimeError(
            f"Checkpoint watcher PID {pid} could not be stopped and reaped; "
            "the checkpoint has been preserved."
        ) from exc
