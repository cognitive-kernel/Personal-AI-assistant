"""Bounded Codex process runner with parent-liveness and descendant ownership checks."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import psutil

try:
    from .autonomous_engineering_process_job import WindowsProcessJob
except ImportError:
    from autonomous_engineering_process_job import WindowsProcessJob


def capture_parent_chain(max_depth: int = 4) -> list[dict[str, float | int]]:
    chain: list[dict[str, float | int]] = []
    process = psutil.Process(os.getpid())
    for index in range(max_depth):
        chain.append({"pid": process.pid, "started_at": float(process.create_time())})
        if index + 1 >= max_depth:
            break
        parent = process.parent()
        if parent is None:
            break
        process = parent
    return chain


def parent_chain_is_alive(chain: list[dict[str, Any]]) -> bool:
    if not isinstance(chain, list) or not chain:
        return False
    seen_pids: set[int] = set()
    for item in chain:
        if not isinstance(item, dict):
            return False
        try:
            pid = item.get("pid")
            started_at = item.get("started_at")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid in seen_pids:
                return False
            if (
                isinstance(started_at, bool)
                or not isinstance(started_at, (int, float))
                or not math.isfinite(float(started_at))
                or started_at <= 0
            ):
                return False
            process = psutil.Process(pid)
            if not process.is_running():
                return False
            actual_started_at = float(process.create_time())
            if not math.isfinite(actual_started_at) or abs(actual_started_at - float(started_at)) > 0.02:
                return False
            seen_pids.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, TypeError, ValueError):
            return False
    return True


def _terminate_process(process: subprocess.Popen[bytes], job: WindowsProcessJob) -> None:
    if os.name == "nt":
        # Closing the kill-on-close job terminates Codex and every descendant.
        job.close()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        time.sleep(0.25)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(
    command: list[str],
    timeout_seconds: int,
    parent_chain: list[dict[str, Any]],
    *,
    attach_current_process: bool = False,
) -> int:
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(argument, str) or not argument for argument in command)
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise ValueError("A command and a positive integer timeout are required.")
    if not parent_chain_is_alive(parent_chain):
        print("Codex launch refused: the owning controller process chain is no longer verifiable.", file=sys.stderr)
        return 125

    job = WindowsProcessJob()
    job.__enter__()
    process: subprocess.Popen[bytes] | None = None
    process_tree_cleaned = False
    owner_bound_windows_job = os.name == "nt" and attach_current_process
    try:
        if owner_bound_windows_job:
            job.attach_current_process_to_owner(int(parent_chain[0]["pid"]))
        creationflags = (
            0 if owner_bound_windows_job
            else WindowsProcessJob.CREATE_SUSPENDED if os.name == "nt"
            else 0
        )
        process = subprocess.Popen(
            command,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        if os.name == "nt" and not owner_bound_windows_job:
            job.assign_and_resume(process)
        assert process.stdout is not None
        events: queue.Queue[bytes | None] = queue.Queue()

        def read_output() -> None:
            try:
                for chunk in iter(process.stdout.readline, b""):
                    events.put(chunk)
            finally:
                events.put(None)

        reader = threading.Thread(target=read_output, name="codex-output-reader", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        stream_closed = False
        while not stream_closed:
            if not parent_chain_is_alive(parent_chain):
                print("Codex stopped because its owning controller exited or its PID identity changed.", file=sys.stderr)
                if owner_bound_windows_job:
                    job.terminate(125)
                    os._exit(125)
                _terminate_process(process, job)
                process_tree_cleaned = True
                return 125
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"Codex exceeded its {timeout_seconds}-second execution budget and was terminated.", file=sys.stderr)
                if owner_bound_windows_job:
                    job.terminate(124)
                    os._exit(124)
                _terminate_process(process, job)
                process_tree_cleaned = True
                return 124
            try:
                chunk = events.get(timeout=min(0.05, remaining))
            except queue.Empty:
                if process.poll() is not None and reader.is_alive() is False:
                    break
                continue
            if chunk is None:
                stream_closed = True
            else:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        while process.poll() is None:
            if not parent_chain_is_alive(parent_chain):
                print("Codex stopped because its owning controller exited or its PID identity changed.", file=sys.stderr)
                if owner_bound_windows_job:
                    job.terminate(125)
                    os._exit(125)
                _terminate_process(process, job)
                process_tree_cleaned = True
                return 125
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"Codex exceeded its {timeout_seconds}-second execution budget and was terminated.", file=sys.stderr)
                if owner_bound_windows_job:
                    job.terminate(124)
                    os._exit(124)
                _terminate_process(process, job)
                process_tree_cleaned = True
                return 124
            time.sleep(min(0.05, remaining))
        exit_code = int(process.wait())
        if owner_bound_windows_job:
            job.terminate(exit_code)
            os._exit(exit_code)
        if os.name != "nt":
            _terminate_process(process, job)
            process_tree_cleaned = True
        return exit_code
    finally:
        if process is not None and not process_tree_cleaned:
            if owner_bound_windows_job and process.poll() is None:
                job.terminate(125)
                os._exit(125)
            if os.name != "nt":
                _terminate_process(process, job)
            elif not owner_bound_windows_job and process.poll() is None:
                _terminate_process(process, job)
        job.close()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("--parent-chain", required=True)
    known, command = parser.parse_known_args()
    if command and command[0] == "--":
        command = command[1:]
    try:
        chain = json.loads(known.parent_chain)
    except (TypeError, ValueError):
        raise SystemExit("Invalid Codex parent-process ownership metadata.")
    if not isinstance(chain, list):
        raise SystemExit("Invalid Codex parent-process ownership metadata.")
    return run(command, known.timeout_seconds, chain, attach_current_process=True)


if __name__ == "__main__":
    raise SystemExit(main())
