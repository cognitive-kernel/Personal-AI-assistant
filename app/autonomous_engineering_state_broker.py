"""Windows named-pipe client for the machine-owned autonomous state broker."""
from __future__ import annotations

import ctypes
import json
import os
import re
import time
from ctypes import wintypes
from typing import Any

_PIPE_NAME = r"\\.\pipe\PersonalAIAssistant.StateBroker.v1"
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_ERROR_IO_PENDING = 997
_ERROR_PIPE_BUSY = 231
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_MAX_REQUEST_BYTES = 16 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class StateBrokerError(RuntimeError):
    """A local machine state-broker request failed safely."""


class StateBrokerConflict(StateBrokerError):
    """The broker refused an operation because durable task state is pending."""

    def __init__(self, message: str, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or []


def _windows_api():
    if os.name != "nt":
        raise StateBrokerError("The machine state broker is available only on Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    )
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetOverlappedResult.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    )
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED))
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = (
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED),
    )
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = (
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED),
    )
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _remaining_ms(deadline: float) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise StateBrokerError("The state-broker request timed out.")
    return max(1, remaining)


def _overlapped_transfer(
    kernel32,
    handle,
    *,
    write: bool,
    buffer,
    size: int,
    deadline: float,
) -> int:
    """Perform one pipe I/O operation with a hard deadline and cancellation."""
    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise StateBrokerError(
            f"Could not create a state-broker I/O event (WinError {ctypes.get_last_error()})."
        )
    operation = _OVERLAPPED()
    operation.hEvent = event
    transferred = wintypes.DWORD()
    try:
        api = kernel32.WriteFile if write else kernel32.ReadFile
        succeeded = api(
            handle, buffer, size, ctypes.byref(transferred), ctypes.byref(operation)
        )
        if not succeeded:
            error = ctypes.get_last_error()
            if error != _ERROR_IO_PENDING:
                raise StateBrokerError(
                    f"Could not {'send' if write else 'read'} the state-broker response "
                    f"(WinError {error})."
                )
            wait_result = kernel32.WaitForSingleObject(event, _remaining_ms(deadline))
            if wait_result != _WAIT_OBJECT_0:
                # Cancel only this operation. Closing the pipe in request() then
                # releases the handle even if the peer has stopped responding.
                kernel32.CancelIoEx(handle, ctypes.byref(operation))
                kernel32.WaitForSingleObject(event, 1000)
                if wait_result == _WAIT_TIMEOUT:
                    raise StateBrokerError("The state-broker request timed out.")
                raise StateBrokerError(
                    f"Waiting for state-broker I/O failed (WinError {ctypes.get_last_error()})."
                )
            if not kernel32.GetOverlappedResult(
                handle, ctypes.byref(operation), ctypes.byref(transferred), False
            ):
                error = ctypes.get_last_error()
                raise StateBrokerError(
                    f"State-broker I/O did not complete (WinError {error})."
                )
        return int(transferred.value)
    finally:
        kernel32.CloseHandle(event)


def request(method: str, path: str, *, timeout_ms: int = 3500) -> Any:
    """Call one fixed broker endpoint; all pipe I/O is bounded by timeout_ms."""
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 250 <= timeout_ms <= 30000:
        raise ValueError("State-broker timeout must be an integer between 250 and 30000 milliseconds.")
    method = str(method).upper()
    if method not in {"GET", "POST"}:
        raise ValueError("Unsupported state-broker method.")
    if not isinstance(path, str):
        raise ValueError("Unsupported state-broker endpoint.")
    allowed = (
        method == "GET" and path in {"/v1/health", "/v1/pending-work", "/v1/progress"}
    ) or (
        method == "GET" and re.fullmatch(r"/v1/diagnostics/[0-9]{1,20}", path) is not None
    ) or (
        method == "POST" and path == "/v1/cleanup"
    )
    if not allowed:
        raise ValueError("Unsupported state-broker endpoint.")
    payload = json.dumps(
        {"version": 1, "method": method, "path": path},
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ValueError("State-broker request is too large.")

    deadline = time.monotonic() + timeout_ms / 1000.0
    kernel32 = _windows_api()
    handle = kernel32.CreateFileW(
        _PIPE_NAME,
        _GENERIC_READ | _GENERIC_WRITE,
        0,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OVERLAPPED,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error == _ERROR_PIPE_BUSY:
            if kernel32.WaitNamedPipeW(_PIPE_NAME, _remaining_ms(deadline)):
                handle = kernel32.CreateFileW(
                    _PIPE_NAME, _GENERIC_READ | _GENERIC_WRITE, 0, None,
                    _OPEN_EXISTING, _FILE_FLAG_OVERLAPPED, None,
                )
                if handle == _INVALID_HANDLE_VALUE:
                    error = ctypes.get_last_error()
            else:
                error = ctypes.get_last_error()
        if handle == _INVALID_HANDLE_VALUE:
            raise StateBrokerError(f"Windows state broker is unavailable (WinError {error}).")

    try:
        buffer = ctypes.create_string_buffer(payload)
        written = _overlapped_transfer(
            kernel32, handle, write=True, buffer=buffer,
            size=len(payload), deadline=deadline,
        )
        if written != len(payload):
            raise StateBrokerError("The state broker accepted only part of the request.")

        response = bytearray()
        while len(response) <= _MAX_RESPONSE_BYTES:
            _remaining_ms(deadline)
            chunk = ctypes.create_string_buffer(8192)
            read = _overlapped_transfer(
                kernel32, handle, write=False, buffer=chunk,
                size=len(chunk), deadline=deadline,
            )
            if read == 0:
                break
            data = chunk.raw[:read]
            newline = data.find(b"\n")
            if newline >= 0:
                response.extend(data[:newline])
                break
            response.extend(data)
        if not response or len(response) > _MAX_RESPONSE_BYTES:
            raise StateBrokerError("The state broker returned an empty or oversized response.")
        try:
            envelope = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateBrokerError("The state broker returned malformed JSON.") from exc
        if not isinstance(envelope, dict) or not isinstance(envelope.get("status"), int):
            raise StateBrokerError("The state broker returned an invalid response envelope.")
        status = envelope["status"]
        result = envelope.get("payload")
        if status == 409 and isinstance(result, dict):
            blockers = result.get("blockers", [])
            if not isinstance(blockers, list):
                blockers = []
            detail = str(result.get("message") or result.get("error") or "operation_conflict")
            raise StateBrokerConflict(detail, [str(item) for item in blockers])
        if status < 200 or status >= 300:
            detail = result.get("error", "request_failed") if isinstance(result, dict) else "request_failed"
            raise StateBrokerError(f"State broker rejected the request ({status}: {detail}).")
        return result
    finally:
        kernel32.CloseHandle(handle)


def health() -> bool:
    try:
        value = request("GET", "/v1/health")
        return isinstance(value, dict) and value.get("ready") is True
    except (OSError, RuntimeError, ValueError):
        return False


def pending_work() -> dict[str, Any]:
    value = request("GET", "/v1/pending-work")
    if not isinstance(value, dict) or value.get("available") is not True:
        raise StateBrokerError("The broker did not return a valid pending-work status.")
    return value


def progress() -> dict[str, Any]:
    value = request("GET", "/v1/progress")
    if not isinstance(value, dict):
        raise StateBrokerError("The broker did not return a valid progress snapshot.")
    return value


def diagnostics(run_id: int) -> dict[str, Any] | None:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    try:
        value = request("GET", f"/v1/diagnostics/{run_id}")
    except StateBrokerError as exc:
        if "404" in str(exc):
            return None
        raise
    return value if isinstance(value, dict) else None


def cleanup() -> dict[str, Any]:
    value = request("POST", "/v1/cleanup")
    if not isinstance(value, dict):
        raise StateBrokerError("The broker did not return a valid cleanup result.")
    if value.get("conflict") is True:
        blockers = value.get("blockers", [])
        raise StateBrokerConflict(
            "Durable task state is pending; cleanup was refused.",
            [str(item) for item in blockers] if isinstance(blockers, list) else [],
        )
    return value
