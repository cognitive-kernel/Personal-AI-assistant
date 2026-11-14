"""Kill-on-owner-exit process job used to supervise Codex and its descendants."""

from __future__ import annotations

import ctypes
import os
from typing import Any


class WindowsProcessJob:
    """A Windows Job Object whose lifetime is owned by the supervising Python process."""

    KILL_ON_JOB_CLOSE = 0x00002000
    CREATE_SUSPENDED = 0x00000004
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    def __init__(self) -> None:
        self._handle: Any = None
        self._kernel32: Any = None

    def __enter__(self) -> "WindowsProcessJob":
        if os.name != "nt":
            return self
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        try:
            class BasicLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class ExtendedLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BasicLimitInformation),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            limits = ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        return self

    def assign_and_resume(self, process: Any) -> None:
        """Assign a CREATE_SUSPENDED child before it can spawn descendants."""
        if os.name != "nt":
            return
        if not self._handle or not self._kernel32:
            raise RuntimeError("The Windows process job has not been initialized.")
        self._kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        process_handle = self._kernel32.OpenProcess(
            self.PROCESS_SET_QUOTA | self.PROCESS_TERMINATE, False, int(process.pid),
        )
        if not process_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process_handle)

        ntdll = ctypes.WinDLL("ntdll")
        resume = ntdll.NtResumeProcess
        resume.argtypes = [ctypes.c_void_p]
        resume.restype = ctypes.c_long
        status = int(resume(process._handle))
        if status < 0:
            raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xffffffff:08x}.")

    def close(self) -> None:
        if self._handle and self._kernel32:
            handle, self._handle = self._handle, None
            if not self._kernel32.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
