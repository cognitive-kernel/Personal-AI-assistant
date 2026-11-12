from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MUTEX_NAME = "Local\\PersonalAIAssistant.AutonomousEngineering.State.v1"
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


@contextmanager
def durable_state_lock(
    state_dir: Path | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[None]:
    """Serialize durable task/PR state transactions across Python and PowerShell processes."""
    timeout = max(0.0, float(timeout_seconds))
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            result = kernel32.WaitForSingleObject(
                handle,
                min(0xFFFFFFFE, int(timeout * 1000)),
            )
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                acquired = True
            elif result == WAIT_TIMEOUT:
                raise TimeoutError(
                    "Timed out waiting for the autonomous engineering durable-state mutex."
                )
            elif result == WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            else:
                raise RuntimeError(
                    f"Unexpected durable-state mutex result: 0x{result:08x}."
                )
            yield
        finally:
            try:
                if acquired and not kernel32.ReleaseMutex(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel32.CloseHandle(handle)
        return

    import fcntl

    if state_dir is None:
        try:
            from .autonomous_engineering_state_paths import execution_state_directory
        except ImportError:  # Direct script execution from the scripts directory.
            from autonomous_engineering_state_paths import execution_state_directory
        state_dir = execution_state_directory()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "autonomous-durable-state.lock"
    deadline = time.monotonic() + timeout
    with lock_path.open("a+b") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out waiting for the autonomous engineering durable-state lock."
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
