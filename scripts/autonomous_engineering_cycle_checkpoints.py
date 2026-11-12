"""Durable checkpoint watcher lifecycle for the autonomous cycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from typing import Any


def checkpoint_command(*args: str, capture: bool = True, run_command, python_executable: str, checkpoint_script: Path) -> subprocess_module.CompletedProcess[str]:
    return run_command([python_executable, str(checkpoint_script), *args], capture=capture)


def restore_checkpoint(base_sha: str, *, os_name: str, programdata: str | None, checkpoint_command) -> bool:
    if os_name != "nt" or not programdata:
        return False
    result = checkpoint_command("restore", "--base-sha", base_sha)
    if result.returncode:
        raise RuntimeError("Could not restore durable engineering checkpoint: " + (result.stderr or result.stdout or "")[-3000:])
    return (result.stdout or "").strip() == "restored"


def start_checkpoint_watcher(base_sha: str, run_id: str, task: int, *, os_module, subprocess_module, sys_module, sysconfig_module, time_module, root: Path, checkpoint_script: Path, execution_state_directory_fn, process_start_time_fn, metadata_confirms_watcher_ready_fn, programdata: str | None) -> subprocess_module.Popen[Any] | None:
    if os_module.name != "nt" or not programdata:
        return None
    # The venv's Windows python.exe may report a launcher PID instead of the
    # child interpreter PID. Launch with the base interpreter so Popen.pid and
    # the watcher's os.getpid() agree, while exposing this venv's dependencies.
    watcher_executable = getattr(sys_module, "_base_executable", sys_module.executable)
    watcher_env = os_module.environ.copy()
    venv_site_packages = sysconfig_module.get_paths().get("purelib")
    if venv_site_packages:
        existing_pythonpath = watcher_env.get("PYTHONPATH", "")
        watcher_env["PYTHONPATH"] = os_module.pathsep.join(
            item for item in (venv_site_packages, existing_pythonpath) if item
        )
    command = [
        watcher_executable, str(checkpoint_script), "watch",
        "--base-sha", base_sha, "--run-id", run_id, "--task", str(task),
    ]
    state_dir = execution_state_directory_fn()
    metadata_path = state_dir / f"worktree-checkpoint-{run_id}-{task}.json"
    marker_id = f"{run_id}-{task}"
    ready_path = state_dir / f"worktree-checkpoint-{marker_id}.ready"
    error_path = state_dir / f"worktree-checkpoint-{marker_id}.error"
    log_path = state_dir / f"worktree-checkpoint-{marker_id}.log"
    state_dir.mkdir(parents=True, exist_ok=True)
    ready_path.unlink(missing_ok=True)
    error_path.unlink(missing_ok=True)
    flags = getattr(subprocess_module, "DETACHED_PROCESS", 0) | getattr(subprocess_module, "CREATE_NEW_PROCESS_GROUP", 0)
    with log_path.open("w", encoding="utf-8") as watcher_log:
        process = subprocess_module.Popen(
            command, cwd=root, stdin=subprocess_module.DEVNULL, stdout=watcher_log,
            stderr=subprocess_module.STDOUT, close_fds=True, creationflags=flags,
            env=watcher_env,
        )
    try:
        expected_watcher_started_at = process_start_time_fn(process.pid)
    except RuntimeError as exc:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess_module.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise RuntimeError(
            "The checkpoint watcher process identity could not be verified; startup was aborted."
        ) from exc
    deadline = time_module.monotonic() + 30
    while time_module.monotonic() < deadline:
        if error_path.is_file():
            detail = error_path.read_text(encoding="utf-8", errors="replace").strip()
            if process.poll() is None:
                process.terminate()
            try:
                log_detail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except OSError:
                log_detail = ""
            raise RuntimeError(
                "The durable checkpoint watcher failed during startup: "
                + detail[-2000:] + (f" Watcher log: {log_detail}" if log_detail else "")
            )
        if process.poll() is not None:
            detail = error_path.read_text(encoding="utf-8", errors="replace").strip() if error_path.is_file() else ""
            try:
                log_detail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except OSError:
                log_detail = ""
            raise RuntimeError(
                "The durable checkpoint watcher exited before becoming ready."
                + (f" Startup error: {detail[-2000:]}" if detail else "")
                + (f" Watcher log: {log_detail}" if log_detail else "")
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_confirms_watcher_ready_fn(
                metadata,
                base_sha=base_sha,
                run_id=run_id,
                task=task,
                watcher_pid=process.pid,
                watcher_started_at=expected_watcher_started_at,
            ):
                return process
        except (OSError, ValueError, AttributeError):
            pass
        time_module.sleep(0.2)
    metadata_detail = ""
    try:
        metadata_detail = metadata_path.read_text(encoding="utf-8")[:1000]
    except OSError:
        pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess_module.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log_detail = ""
    try:
        log_detail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        pass
    raise RuntimeError(
        "The durable checkpoint watcher did not become ready within thirty seconds. "
        f"Metadata: {metadata_detail or 'unavailable'}. "
        f"Watcher log: {log_detail or 'unavailable'}"
    )


def stop_checkpoint_watcher(
    watcher, base_sha: str, run_id: str, task: int, *, subprocess_module, os_name: str, programdata: str | None, checkpoint_command,
) -> None:
    if watcher is not None and watcher.poll() is None:
        watcher.terminate()
        try:
            watcher.wait(timeout=5)
        except subprocess_module.TimeoutExpired:
            watcher.kill()
            watcher.wait(timeout=5)
    if os_name == "nt" and programdata:
        stopped = checkpoint_command("stop", "--run-id", run_id, "--task", str(task))
        if stopped.returncode:
            raise RuntimeError("Could not stop the durable checkpoint watcher.")
        saved = checkpoint_command(
            "snapshot", "--base-sha", base_sha, "--run-id", run_id, "--task", str(task),
        )
        if saved.returncode:
            raise RuntimeError("Could not save the final durable worktree checkpoint.")


def clear_checkpoint(run_id: str | None = None, task: int | None = None, *, os_name: str, programdata: str | None, checkpoint_command) -> None:
    if os_name == "nt" and programdata:
        if run_id is None and task is None:
            result = checkpoint_command("clear-latest")
        elif run_id is not None and task is not None:
            result = checkpoint_command("clear", "--run-id", run_id, "--task", str(task))
        else:
            raise ValueError("Checkpoint cleanup requires both run_id and task, or neither for latest-owner cleanup.")
        if result.returncode:
            raise RuntimeError("Could not clear the completed durable worktree checkpoint.")
