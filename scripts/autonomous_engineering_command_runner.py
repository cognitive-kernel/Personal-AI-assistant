"""Process-tree-scoped command execution with bounded deadlines.

Every controller subprocess is placed in an owned process group (POSIX) or
kill-on-close Windows Job Object. Descendants cannot outlive a completed or
timed-out command and continue mutating the workspace.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

try:
    from .autonomous_engineering_process_job import WindowsProcessJob
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_process_job import WindowsProcessJob


def _terminate_posix_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    capture: bool,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run one command and guarantee its process tree ends before returning."""
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(argument, str) or not argument for argument in command)
    ):
        raise ValueError("A non-empty command containing only non-empty strings is required.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("Command timeout must be a positive integer.")

    job = WindowsProcessJob()
    job.__enter__()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=None,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=WindowsProcessJob.CREATE_SUSPENDED if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if os.name == "nt":
            job.assign_and_resume(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                job.terminate(124)
            else:
                _terminate_posix_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            raise subprocess.TimeoutExpired(
                command, timeout_seconds,
                output=stdout if stdout is not None else exc.output,
                stderr=stderr if stderr is not None else exc.stderr,
            ) from exc

        # A successful parent may leave background descendants behind. Closing
        # the Windows job or signalling the POSIX process group reaps that tail.
        if os.name != "nt":
            _terminate_posix_group(process)
        return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)
    finally:
        if process is not None and process.poll() is None:
            if os.name == "nt":
                job.close()
            else:
                _terminate_posix_group(process)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        job.close()
