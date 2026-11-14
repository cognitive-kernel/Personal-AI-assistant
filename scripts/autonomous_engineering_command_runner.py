"""Process-tree-scoped command execution with bounded deadlines.

Every controller subprocess is placed in an owned process group (POSIX) or
kill-on-close Windows Job Object. The owner waits until termination is
observable before releasing its repository lock or returning to its caller.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


try:
    from .autonomous_engineering_process_job import WindowsProcessJob
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_process_job import WindowsProcessJob

try:
    from .autonomous_engineering_process_group import terminate_process_group
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_process_group import terminate_process_group


def _terminate_posix_group(process: subprocess.Popen[str]) -> None:
    terminate_process_group(process.pid)
    if process.poll() is None:
        process.wait(timeout=5)


def _terminate_windows_job(job: WindowsProcessJob, exit_code: int) -> None:
    if job.wait_for_empty(0):
        return
    job.terminate(exit_code)
    if not job.wait_for_empty(5):
        raise RuntimeError("Could not confirm termination of every process in the Windows Job Object.")


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    capture: bool,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run one command and wait for every owned process to exit before returning."""
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
                _terminate_windows_job(job, 124)
            else:
                _terminate_posix_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    process.kill()
                stdout, stderr = process.communicate(timeout=5)
            raise subprocess.TimeoutExpired(
                command, timeout_seconds,
                output=stdout if stdout is not None else exc.output,
                stderr=stderr if stderr is not None else exc.stderr,
            ) from exc

        # A successful parent may leave background descendants behind.
        if os.name == "nt":
            _terminate_windows_job(job, 0)
        else:
            _terminate_posix_group(process)
        return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)
    finally:
        try:
            if process is not None:
                if os.name == "nt":
                    try:
                        _terminate_windows_job(job, 125)
                    finally:
                        if process.poll() is None:
                            try:
                                process.wait(timeout=1)
                            except subprocess.TimeoutExpired:
                                # Assignment itself can fail. A suspended process
                                # that never entered the job must still be killed.
                                process.kill()
                                process.wait(timeout=5)
                else:
                    _terminate_posix_group(process)
        finally:
            # Always close the Job Object, even if termination confirmation
            # fails; KILL_ON_JOB_CLOSE is the last-resort containment boundary.
            job.close()
