from __future__ import annotations

import ctypes
import json
import math
import os
import shlex
import signal
import select
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _windows_process_snapshot(pid: int) -> dict[str, Any] | None:
    """Read PID, creation time and command line using Windows' built-in CIM provider."""
    script = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$target = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = __PID__"
if ($null -eq $target) { exit 3 }
$createdValue = $target.CreationDate
if ($createdValue -is [DateTimeOffset]) {
    $created = $createdValue.ToUniversalTime().ToString('o')
} elseif ($createdValue -is [DateTime]) {
    $created = $createdValue.ToUniversalTime().ToString('o')
} else {
    $createdText = [string]$createdValue
    $parsed = [DateTimeOffset]::MinValue
    if ([DateTimeOffset]::TryParse($createdText, [ref]$parsed)) {
        $created = $parsed.ToUniversalTime().ToString('o')
    } else {
        $created = [System.Management.ManagementDateTimeConverter]::ToDateTime($createdText).ToUniversalTime().ToString('o')
    }
}
[pscustomobject]@{
    pid = [int]$target.ProcessId
    created_at = $created
    command_line = [string]$target.CommandLine
} | ConvertTo-Json -Compress
""".replace("__PID__", str(pid))
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    powershell_path = (
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system_root
        else None
    )
    powershell_executable = (
        str(powershell_path)
        if powershell_path is not None and powershell_path.is_file()
        else "powershell.exe"
    )
    try:
        result = subprocess.run(
            [powershell_executable, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Windows process metadata could not be queried with the built-in CIM provider "
            f"({type(exc).__name__}: {exc})."
        ) from exc
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        raise PermissionError(
            f"Windows denied process metadata access for PID {pid}: {(result.stderr or result.stdout)[-500:]}"
        )
    try:
        payload = json.loads(result.stdout)
        created_text = payload["created_at"]
        created_at = datetime.fromisoformat(str(created_text).replace("Z", "+00:00")).timestamp()
        command_line = payload.get("command_line")
        if not isinstance(command_line, str) or not command_line.strip():
            command: list[str] = []
        else:
            command = [token.strip('"') for token in shlex.split(command_line, posix=False)]
        returned_pid = int(payload["pid"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Windows returned malformed process metadata for PID {pid}.") from exc
    if returned_pid != pid or not math.isfinite(created_at) or created_at <= 0:
        raise RuntimeError(f"Windows returned inconsistent process identity for PID {pid}.")
    return {"created_at": created_at, "command": command}


def _linux_process_snapshot(pid: int) -> dict[str, Any] | None:
    """Read process creation time and argv from procfs without third-party packages."""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat_text = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PermissionError(f"Cannot read process metadata for PID {pid}.") from exc

    closing = stat_text.rfind(")")
    if closing < 0:
        raise RuntimeError(f"Linux returned malformed process metadata for PID {pid}.")
    fields = stat_text[closing + 2 :].split()
    if fields and fields[0] in {"Z", "X"}:
        # A zombie has exited and cannot write; its parent may not have reaped it yet.
        return None
    try:
        start_ticks = int(fields[19])  # proc stat field 22 (starttime)
        boot_line = next(line for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
                         if line.startswith("btime "))
        boot_time = int(boot_line.split()[1])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        created_at = boot_time + start_ticks / ticks_per_second
        raw_command = Path(f"/proc/{pid}/cmdline").read_bytes()
        command = [
            part.decode("utf-8", errors="replace")
            for part in raw_command.split(b"\0")
            if part
        ]
    except (OSError, StopIteration, IndexError, ValueError, ZeroDivisionError) as exc:
        raise RuntimeError(f"Linux returned incomplete process identity for PID {pid}.") from exc
    if not math.isfinite(created_at) or created_at <= 0:
        raise RuntimeError(f"Linux returned an invalid process creation time for PID {pid}.")
    return {"created_at": created_at, "command": command}


def _read_process_snapshot(pid: int) -> dict[str, Any] | None:
    if os.name == "nt":
        return _windows_process_snapshot(pid)
    if Path("/proc").is_dir():
        return _linux_process_snapshot(pid)
    raise RuntimeError("Process identity verification is supported only on Windows and Linux.")


def process_start_time(pid: int) -> float:
    """Return the OS creation time used to distinguish a process from a reused PID."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("Cannot verify an invalid OS process ID.")
    snapshot = _read_process_snapshot(pid)
    if snapshot is None:
        raise RuntimeError(f"Cannot verify OS process creation time for PID {pid}; the process is not running.")
    value = float(snapshot.get("created_at", 0))
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


def _filetime_to_unix_seconds(value: Any) -> float:
    low = int(value.dwLowDateTime)
    high = int(value.dwHighDateTime)
    ticks = (high << 32) | low
    return ticks / 10_000_000 - 11_644_473_600


def _terminate_windows_process(
    pid: int,
    timeout_seconds: float,
    expected_started_at: float,
) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    process_terminate = 0x0001
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    handle = kernel32.OpenProcess(
        process_terminate | process_query_limited_information | synchronize,
        False,
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:  # invalid parameter / not found: already exited
            return
        raise OSError(error, f"OpenProcess failed for watcher PID {pid}.")
    try:
        if kernel32.WaitForSingleObject(handle, 0) == wait_object_0:
            return
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, f"GetProcessTimes failed for watcher PID {pid}.")
        actual_started_at = _filetime_to_unix_seconds(creation)
        if abs(actual_started_at - expected_started_at) > 0.02:
            raise RuntimeError(
                f"Watcher PID {pid} was reused before termination; refusing to terminate a different process."
            )
        if not kernel32.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if kernel32.WaitForSingleObject(handle, 0) == wait_object_0:
                return
            raise OSError(error, f"TerminateProcess failed for watcher PID {pid}.")
        result = kernel32.WaitForSingleObject(
            handle, max(100, int(timeout_seconds * 1000))
        )
        if result == wait_timeout:
            raise TimeoutError(f"Watcher PID {pid} did not exit before the termination deadline.")
        if result != wait_object_0:
            error = ctypes.get_last_error()
            raise OSError(error, f"WaitForSingleObject failed for watcher PID {pid}.")
    finally:
        kernel32.CloseHandle(handle)


def _terminate_linux_process(
    pid: int,
    timeout_seconds: float,
    expected_started_at: float,
) -> None:
    """Terminate through a pidfd so a reused PID can never receive the signal."""
    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_open) or not callable(pidfd_send_signal):
        raise RuntimeError("Safe Linux watcher termination requires pidfd support; refusing PID-based kill.")
    try:
        pidfd = pidfd_open(pid, 0)
    except ProcessLookupError:
        return
    try:
        snapshot = _linux_process_snapshot(pid)
        if snapshot is None:
            return
        actual_started_at = float(snapshot.get("created_at", 0))
        if not math.isfinite(actual_started_at) or abs(actual_started_at - expected_started_at) > 0.02:
            raise RuntimeError(
                f"Watcher PID {pid} was reused before termination; refusing to signal a different process."
            )
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        try:
            pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            return
        wait_ms = max(100, int(timeout_seconds * 1000))
        if poller.poll(wait_ms):
            return
        try:
            pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
        except ProcessLookupError:
            return
        if poller.poll(wait_ms):
            return
        raise TimeoutError(f"Watcher PID {pid} could not be reaped after SIGKILL.")
    finally:
        os.close(pidfd)


def _terminate_process(
    pid: int,
    timeout_seconds: float,
    expected_started_at: float,
) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Process termination timeout must be a positive finite number.")
    if (
        isinstance(expected_started_at, bool)
        or not isinstance(expected_started_at, (int, float))
        or not math.isfinite(expected_started_at)
        or expected_started_at <= 0
    ):
        raise ValueError("Expected watcher process start time must be a positive finite number.")
    if os.name == "nt":
        _terminate_windows_process(pid, timeout_seconds, float(expected_started_at))
    elif Path("/proc").is_dir():
        _terminate_linux_process(pid, timeout_seconds, float(expected_started_at))
    else:
        raise RuntimeError("Process termination is supported only on Windows and Linux.")


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
        snapshot = _read_process_snapshot(pid)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot inspect checkpoint watcher PID {pid}; recovery is blocked to prevent concurrent writes."
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot verify checkpoint watcher PID {pid}; recovery is blocked to prevent concurrent writes."
        ) from exc
    if snapshot is None:
        return

    try:
        actual_start = float(snapshot["created_at"])
        command = snapshot["command"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Cannot verify checkpoint watcher PID {pid}; process metadata is incomplete.") from exc
    if not math.isfinite(actual_start) or actual_start <= 0 or not isinstance(command, list):
        raise RuntimeError(f"Cannot verify checkpoint watcher PID {pid}; process metadata is invalid.")

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

    command_matches = _owns_watcher_command(
        command,
        script_path=script_path,
        run_id=run_id,
        task=task,
        base_sha=base_sha,
    )
    if abs(actual_start - float(expected_start)) > 0.02:
        if command_matches:
            raise RuntimeError(
                f"Checkpoint PID {pid} matches the recorded watcher command but its process creation time differs; "
                "ownership is ambiguous, so recovery and cleanup are blocked."
            )
        return
    if not command_matches:
        raise RuntimeError(
            f"Checkpoint PID {pid} is alive but its command line does not match the recorded run/task; "
            "refusing recovery or cleanup."
        )

    try:
        _terminate_process(pid, timeout_seconds, actual_start)
    except (PermissionError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"Checkpoint watcher PID {pid} could not be stopped and reaped; "
            "the checkpoint has been preserved."
        ) from exc
