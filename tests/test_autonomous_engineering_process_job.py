from __future__ import annotations

import os
import subprocess
import sys

import pytest

from scripts.autonomous_engineering_process_job import WindowsProcessJob


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
def test_windows_job_allows_short_lived_descendant_to_exit_after_wrapper():
    child_code = "import time; time.sleep(0.5)"
    wrapper_code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )

    with WindowsProcessJob() as job:
        wrapper = subprocess.Popen(
            [sys.executable, "-c", wrapper_code],
            creationflags=WindowsProcessJob.CREATE_SUSPENDED,
        )
        try:
            job.assign_and_resume(wrapper)
            assert wrapper.wait(timeout=10) == 0
            # The wrapper has exited, but its inherited child needs a brief
            # natural shutdown window before the Job Object becomes empty.
            assert job.terminate_remaining_processes(125, grace_seconds=3.0) is False
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)
