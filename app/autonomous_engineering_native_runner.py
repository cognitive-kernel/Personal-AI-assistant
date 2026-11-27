"""Read-only local ownership evidence for a native Runner listener."""
from __future__ import annotations

import ctypes
import math
import os
from ctypes import wintypes
from pathlib import Path
from typing import Literal

import psutil

from .autonomous_engineering_native_config import validate_native_state_root

LocalOwnership = Literal["verified", "not_found", "mismatch", "ambiguous", "unverifiable"]
_LISTENER_NAMES = {"runner.listener", "runner.listener.exe"}


def _windows_process_user_sid(pid: int) -> bytes:
    """Read the kernel process token's user SID without resolving account names."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    security.GetTokenInformation.restype = wintypes.BOOL
    security.GetLengthSid.argtypes = (wintypes.LPVOID,)
    security.GetLengthSid.restype = wintypes.DWORD

    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    token = wintypes.HANDLE()
    try:
        if not security.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if ctypes.get_last_error() != 122 or not 0 < size.value <= 4096:  # ERROR_INSUFFICIENT_BUFFER
            raise OSError("Could not size user token information.")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER begins with SID_AND_ATTRIBUTES, whose first member is PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents.value
        length = security.GetLengthSid(sid) if sid else 0
        if not length:
            raise OSError("Could not read user token SID.")
        return ctypes.string_at(sid, length)
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def _same_process_user(process: psutil.Process) -> bool:
    if os.name == "nt":
        return _windows_process_user_sid(process.pid) == _windows_process_user_sid(os.getpid())
    return process.uids().effective == os.geteuid()


def _matches_native_environment(process: psutil.Process, state_root: Path) -> bool:
    # Read this only after the OS same-user check. Keep just the two settings
    # needed for the comparison; never return or log the process environment.
    environment = process.environ()
    backend = environment.get("AUTONOMOUS_ENGINEERING_STATE_BACKEND", "")
    configured = environment.get("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "")
    del environment
    path = Path(configured)
    return (
        backend.strip().casefold() == "native"
        and path.is_absolute()
        and path.resolve() == state_root
    )


def _inspect_listener(process: psutil.Process, state_root: Path) -> LocalOwnership:
    try:
        started_at = process.create_time()
        if not math.isfinite(started_at) or started_at <= 0:
            return "unverifiable"
        if Path(process.exe()).name.casefold() not in _LISTENER_NAMES or not _same_process_user(process):
            return "mismatch"
        if not process.is_running():
            return "not_found"
        if process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
            return "not_found"
        if not _matches_native_environment(process, state_root):
            return "mismatch"
        # Reopen by PID after all observations so psutil cannot reuse the first
        # object's cached creation time when checking for PID reuse.
        fresh = psutil.Process(process.pid)
        if fresh.create_time() != started_at or not fresh.is_running():
            return "unverifiable"
        if fresh.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
            return "not_found"
        return "verified"
    except psutil.NoSuchProcess:
        return "not_found"
    except (psutil.Error, OSError, RuntimeError, ValueError):
        return "unverifiable"


def native_runner_status(state_root_provider) -> dict[str, bool | LocalOwnership]:
    """Report local listener ownership without granting workflow capability."""
    try:
        state_root = state_root_provider()
        validate_native_state_root(state_root)
        state_root = state_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return {"running": False, "process_detected": False, "local_ownership": "unverifiable"}

    evidence = [
        _inspect_listener(process, state_root)
        for process in psutil.process_iter(["name"])
        if (process.info.get("name") or "").casefold() in _LISTENER_NAMES
    ]
    if evidence.count("verified") > 1:
        ownership = "ambiguous"
    elif "unverifiable" in evidence:
        ownership = "unverifiable"
    elif "verified" in evidence:
        ownership = "verified"
    elif "mismatch" in evidence:
        ownership = "mismatch"
    else:
        ownership = "not_found"
    return {
        "running": ownership == "verified",
        "process_detected": any(item != "not_found" for item in evidence),
        "local_ownership": ownership,
    }
