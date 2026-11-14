"""POSIX process-group termination and drain primitives.

The caller owns the group leader and must wait for the leader separately.
This module only guarantees that no live member remains in the supplied group.
"""
from __future__ import annotations

import math
import os
import signal
import time

import psutil


def process_group_has_live_members(process_group_id: int) -> bool:
    if isinstance(process_group_id, bool) or not isinstance(process_group_id, int) or process_group_id <= 0:
        raise ValueError("A positive process-group ID is required.")
    for process in psutil.process_iter(["pid", "status"]):
        try:
            if os.getpgid(process.pid) != process_group_id:
                continue
            if process.info.get("status") != psutil.STATUS_ZOMBIE:
                return True
        except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
            continue
        except psutil.AccessDenied:
            try:
                if os.getpgid(process.pid) == process_group_id:
                    return True
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                continue
    return False


def wait_for_process_group_exit(process_group_id: int, timeout_seconds: float) -> bool:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds < 0
    ):
        raise ValueError("Process-group wait timeout must be a non-negative number.")
    deadline = time.monotonic() + timeout_seconds
    while process_group_has_live_members(process_group_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def terminate_process_group(
    process_group_id: int,
    *,
    grace_seconds: float = 0.5,
    timeout_seconds: float = 5.0,
) -> bool:
    """Terminate a group, confirm no live members remain, and return whether it had members."""
    for name, value in (("grace_seconds", grace_seconds), ("timeout_seconds", timeout_seconds)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative finite number.")
    had_live_members = process_group_has_live_members(process_group_id)
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return had_live_members
    wait_for_process_group_exit(process_group_id, grace_seconds)
    # Escalate unconditionally after the grace window to close fork-vs-scan races.
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if not wait_for_process_group_exit(process_group_id, timeout_seconds):
        raise RuntimeError(
            f"Could not confirm termination of every live process in group {process_group_id}."
        )
    return had_live_members
