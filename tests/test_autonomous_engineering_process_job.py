from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import psutil
import pytest

from scripts.autonomous_engineering_process_job import WindowsProcessJob


def _windows_job_process_details(job: WindowsProcessJob) -> list[str]:
    """Describe current Job Object members when a Windows integration test fails."""
    class BasicProcessIdList(ctypes.Structure):
        _fields_ = [
            ("number_of_assigned_processes", ctypes.c_uint32),
            ("number_of_process_ids", ctypes.c_uint32),
            ("process_ids", ctypes.c_size_t * 64),
        ]

    info = BasicProcessIdList()
    returned = ctypes.c_uint32()
    query = job._kernel32.QueryInformationJobObject
    query.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
    ]
    query.restype = ctypes.c_int
    if not query(
        job._handle, 3, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    descriptions = []
    for pid in list(info.process_ids)[:info.number_of_process_ids]:
        try:
            process = psutil.Process(int(pid))
            descriptions.append(
                f"{pid}:{process.name()} parent={process.ppid()} cmd={process.cmdline()!r}"
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            descriptions.append(f"{pid}:exited-or-inaccessible")
    return descriptions


def test_terminate_remaining_processes_accepts_an_empty_job(monkeypatch):
    job = WindowsProcessJob()
    waits = []
    terminated = []

    def wait_for_empty(timeout_seconds):
        waits.append(timeout_seconds)
        return True

    monkeypatch.setattr(job, "wait_for_empty", wait_for_empty)
    monkeypatch.setattr(job, "terminate", lambda exit_code: terminated.append(exit_code))

    assert job.terminate_remaining_processes(125, grace_seconds=2.0) is False
    assert waits == [2.0]
    assert terminated == []


def test_terminate_remaining_processes_waits_for_natural_descendant_exit(monkeypatch):
    job = WindowsProcessJob()
    waits = []
    terminated = []

    def wait_for_empty(timeout_seconds):
        waits.append(timeout_seconds)
        # The descendant exits during the bounded grace period.
        return timeout_seconds == 2.0

    monkeypatch.setattr(job, "wait_for_empty", wait_for_empty)
    monkeypatch.setattr(job, "terminate", lambda exit_code: terminated.append(exit_code))

    assert job.terminate_remaining_processes(125, grace_seconds=2.0) is False
    assert waits == [2.0]
    assert terminated == []


def test_terminate_remaining_processes_kills_descendants_that_exceed_grace(monkeypatch):
    job = WindowsProcessJob()
    waits = []
    terminated = []

    def wait_for_empty(timeout_seconds):
        waits.append(timeout_seconds)
        return timeout_seconds == 5.0

    monkeypatch.setattr(job, "wait_for_empty", wait_for_empty)
    monkeypatch.setattr(job, "terminate", lambda exit_code: terminated.append(exit_code))

    assert job.terminate_remaining_processes(
        125, timeout_seconds=5.0, grace_seconds=2.0
    ) is True
    assert waits == [2.0, 5.0]
    assert terminated == [125]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration test.")
def test_windows_job_allows_short_lived_descendant_to_exit_after_wrapper(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        "Path(" + repr(str(child_pid_file)) + ").write_text(str(os.getpid())); "
        "time.sleep(1.0)"
    )
    wrapper_code = "\\n".join([
        "import pathlib,subprocess,sys,time",
        "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        "deadline=time.monotonic()+10",
        "path=pathlib.Path(" + repr(str(child_pid_file)) + ")",
        "while not path.exists() and time.monotonic()<deadline:",
        "    time.sleep(0.01)",
    ])

    with WindowsProcessJob() as job:
        wrapper = subprocess.Popen(
            [sys.executable, "-c", wrapper_code],
            creationflags=WindowsProcessJob.CREATE_SUSPENDED,
        )
        try:
            job.assign_and_resume(wrapper)
            assert wrapper.wait(timeout=10) == 0
            assert child_pid_file.is_file(), "The wrapper did not launch its child."
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            members_before_drain = _windows_job_process_details(job)
            assert child_pid in [
                int(description.split(":", 1)[0]) for description in members_before_drain
            ], f"The child was not retained in the owning Job Object: {members_before_drain!r}"
            forced_termination = job.terminate_remaining_processes(
                125, grace_seconds=3.0
            )
            assert forced_termination is False, (
                "A short-lived child did not exit naturally during the grace window; "
                f"Job Object members before drain: {members_before_drain!r}"
            )
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)
