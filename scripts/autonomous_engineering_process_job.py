"""Kill-on-owner-exit process job used to supervise Codex and its descendants."""

from __future__ import annotations

import ctypes
import math
import os
import time
from typing import Any


class WindowsProcessJob:
    """A Windows Job Object whose lifetime is owned by the supervising Python process."""

    KILL_ON_JOB_CLOSE = 0x00002000
    CREATE_SUSPENDED = 0x00000004
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    PROCESS_DUP_HANDLE = 0x0040
    DUPLICATE_SAME_ACCESS = 0x00000002

    def __init__(self) -> None:
        self._handle: Any = None
        self._kernel32: Any = None
        self._process_handles: dict[int, int] = {}

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

    def attach_current_process_to_owner(self, owner_pid: int) -> None:
        """Place this supervisor in the job and pin a job handle in its owner."""
        if os.name != "nt":
            return
        if not self._handle or not self._kernel32:
            raise RuntimeError("The Windows process job has not been initialized.")
        if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
            raise ValueError("A valid owning process ID is required.")

        self._kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

        current_handle = self._kernel32.OpenProcess(
            self.PROCESS_SET_QUOTA | self.PROCESS_TERMINATE, False, os.getpid(),
        )
        if not current_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, current_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(current_handle)

        owner_handle = self._kernel32.OpenProcess(
            self.PROCESS_DUP_HANDLE, False, owner_pid,
        )
        if not owner_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self._kernel32.GetCurrentProcess.argtypes = []
            self._kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            self._kernel32.DuplicateHandle.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                ctypes.c_int, ctypes.c_uint32,
            ]
            self._kernel32.DuplicateHandle.restype = ctypes.c_int
            owner_job_handle = ctypes.c_void_p()
            if not self._kernel32.DuplicateHandle(
                self._kernel32.GetCurrentProcess(), self._handle, owner_handle,
                ctypes.byref(owner_job_handle), 0, False, self.DUPLICATE_SAME_ACCESS,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not owner_job_handle.value:
                raise RuntimeError("The owning controller did not receive a valid Job Object handle.")
        finally:
            self._kernel32.CloseHandle(owner_handle)

    def terminate(self, exit_code: int) -> None:
        """Terminate this supervisor and every descendant in the owned job."""
        if os.name != "nt":
            return
        if not self._handle or not self._kernel32:
            raise RuntimeError("The Windows process job has not been initialized.")
        self._kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.TerminateJobObject.restype = ctypes.c_int
        if not self._kernel32.TerminateJobObject(self._handle, int(exit_code) & 0xFFFFFFFF):
            raise ctypes.WinError(ctypes.get_last_error())

    SYNCHRONIZE = 0x00100000
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_INFORMATION = 0x0400
    ERROR_INVALID_PARAMETER = 87
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF

    def _job_process_ids(self) -> list[int]:
        """Return the PIDs currently assigned to this Job Object."""
        if os.name != "nt":
            return []
        if not self._handle or not self._kernel32:
            raise RuntimeError("The Windows process job has not been initialized.")

        self._kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
        ]
        self._kernel32.QueryInformationJobObject.restype = ctypes.c_int

        pointer_size = ctypes.sizeof(ctypes.c_size_t)
        capacity = 16
        while True:
            buffer_size = 8 + (capacity * pointer_size)
            buffer = ctypes.create_string_buffer(buffer_size)
            returned_length = ctypes.c_uint32()
            if self._kernel32.QueryInformationJobObject(
                self._handle,
                3,  # JobObjectBasicProcessIdList
                ctypes.byref(buffer),
                buffer_size,
                ctypes.byref(returned_length),
            ):
                count = int(ctypes.c_uint32.from_buffer(buffer, 4).value)
                if count > capacity:
                    capacity = count
                    continue
                pids = ctypes.cast(
                    ctypes.addressof(buffer) + 8,
                    ctypes.POINTER(ctypes.c_size_t),
                )
                return [int(pids[index]) for index in range(count) if pids[index]]

            error = ctypes.get_last_error()
            if error not in {122, 234}:  # ERROR_INSUFFICIENT_BUFFER / ERROR_MORE_DATA
                raise ctypes.WinError(error)
            required = int(returned_length.value)
            required_capacity = (
                max(1, (required - 8 + pointer_size - 1) // pointer_size)
                if required > 8 else capacity * 2
            )
            capacity = max(capacity * 2, required_capacity)

    def _open_job_process_handle(self, pid: int) -> int | None:
        """Pin a PID only after verifying that its process object belongs to this job."""
        access = self.SYNCHRONIZE | self.PROCESS_TERMINATE | self.PROCESS_QUERY_INFORMATION
        self._kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32,
        ]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        process_handle = self._kernel32.OpenProcess(access, False, pid)
        if not process_handle:
            error = ctypes.get_last_error()
            if error == self.ERROR_INVALID_PARAMETER:
                # The process may have exited between the PID-list snapshot and OpenProcess.
                return None
            raise ctypes.WinError(error)

        self._kernel32.IsProcessInJob.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
        ]
        self._kernel32.IsProcessInJob.restype = ctypes.c_int
        belongs_to_job = ctypes.c_int()
        if not self._kernel32.IsProcessInJob(
            process_handle, self._handle, ctypes.byref(belongs_to_job),
        ):
            error = ctypes.get_last_error()
            self._kernel32.CloseHandle(process_handle)
            raise ctypes.WinError(error)
        if not belongs_to_job.value:
            self._kernel32.CloseHandle(process_handle)
            return None
        return int(process_handle)

    def _track_job_processes(self, current_pids: set[int]) -> None:
        for pid in current_pids:
            if pid in self._process_handles:
                continue
            process_handle = self._open_job_process_handle(pid)
            if process_handle is not None:
                self._process_handles[pid] = process_handle

    def _close_tracked_process_handle(self, pid: int) -> None:
        process_handle = self._process_handles.pop(pid)
        if not self._kernel32.CloseHandle(process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_for_empty(self, timeout_seconds: float) -> bool:
        """Wait for all observed job members to leave the job and signal process exit.

        Job PID-list removal alone is not proof that the process object is signaled.
        Keep identity-bound handles for observed members and wait on each process.
        """
        if os.name != "nt":
            return True
        if not self._handle or not self._kernel32:
            raise RuntimeError("The Windows process job has not been initialized.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("Job wait timeout must be a non-negative number.")

        self._kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            current_pids = set(self._job_process_ids())
            self._track_job_processes(current_pids)

            for pid, process_handle in list(self._process_handles.items()):
                result = self._kernel32.WaitForSingleObject(process_handle, 0)
                if result == self.WAIT_OBJECT_0 and pid not in current_pids:
                    self._close_tracked_process_handle(pid)
                elif result == self.WAIT_TIMEOUT:
                    continue
                elif result == self.WAIT_FAILED:
                    raise ctypes.WinError(ctypes.get_last_error())
                elif result != self.WAIT_OBJECT_0:
                    raise RuntimeError(f"Unexpected process wait result: 0x{result:08x}.")

            if not current_pids and not self._process_handles:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))

    def _terminate_and_drain_job(self, exit_code: int, timeout_seconds: float) -> None:
        """Terminate observed members individually, then use job termination as a safety net."""
        deadline = time.monotonic() + float(timeout_seconds)
        termination_requested: set[int] = set()
        self._kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.TerminateProcess.restype = ctypes.c_int

        while True:
            current_pids = set(self._job_process_ids())
            self._track_job_processes(current_pids)

            for pid, process_handle in list(self._process_handles.items()):
                result = self._kernel32.WaitForSingleObject(process_handle, 0)
                if result == self.WAIT_FAILED:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == self.WAIT_OBJECT_0 or pid in termination_requested:
                    continue
                if result != self.WAIT_TIMEOUT:
                    raise RuntimeError(f"Unexpected process wait result: 0x{result:08x}.")
                if not self._kernel32.TerminateProcess(
                    process_handle, int(exit_code) & 0xFFFFFFFF,
                ):
                    error = ctypes.get_last_error()
                    if self._kernel32.WaitForSingleObject(process_handle, 0) != self.WAIT_OBJECT_0:
                        raise ctypes.WinError(error)
                termination_requested.add(pid)

            # Re-query after requesting termination. New descendants created just
            # before a parent was stopped must be captured and stopped as well.
            remaining_pids = set(self._job_process_ids())
            if not remaining_pids:
                # PID-list removal can precede process-object signaling. Wait for
                # every retained process handle before returning.
                all_signaled = True
                for pid, process_handle in list(self._process_handles.items()):
                    result = self._kernel32.WaitForSingleObject(process_handle, 0)
                    if result == self.WAIT_OBJECT_0:
                        self._close_tracked_process_handle(pid)
                    elif result == self.WAIT_TIMEOUT:
                        all_signaled = False
                    elif result == self.WAIT_FAILED:
                        raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        raise RuntimeError(f"Unexpected process wait result: 0x{result:08x}.")
                if all_signaled and not self._process_handles:
                    # Final safety net after the observed job tree has drained.
                    self.terminate(exit_code)
                    return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Do not claim a successful drain when process handles remain unsignaled.
                self.terminate(exit_code)
                raise RuntimeError(
                    "Could not confirm termination of every observed process in the Windows Job Object."
                )
            time.sleep(min(0.01, remaining))

    def terminate_remaining_processes(
        self,
        exit_code: int,
        timeout_seconds: float = 5.0,
        grace_seconds: float = 2.0,
    ) -> bool:
        """Allow natural descendant exit briefly, then terminate and drain leftovers.

        Returns True only when a remaining job member had to be terminated.
        A forced stop tracks each member by process handle, terminates observed
        members individually, and confirms each process object is signaled.
        """
        if self.wait_for_empty(grace_seconds):
            return False
        self._terminate_and_drain_job(exit_code, timeout_seconds)
        return True

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
        first_error: OSError | None = None
        if self._handle and self._kernel32:
            handle, self._handle = self._handle, None
            if not self._kernel32.CloseHandle(handle):
                first_error = ctypes.WinError(ctypes.get_last_error())
        if self._kernel32:
            for process_handle in self._process_handles.values():
                if not self._kernel32.CloseHandle(process_handle) and first_error is None:
                    first_error = ctypes.WinError(ctypes.get_last_error())
        self._process_handles.clear()
        if first_error is not None:
            raise first_error

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
