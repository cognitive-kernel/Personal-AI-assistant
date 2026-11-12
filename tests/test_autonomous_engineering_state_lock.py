from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.autonomous_engineering_state_lock import (
    MUTEX_NAME,
    durable_state_lock,
)


def test_durable_state_lock_excludes_other_processes_and_releases_after_scope(
    tmp_path: Path,
):
    state_dir = tmp_path / "state"
    code = (
        "import time; "
        "from pathlib import Path; "
        "from scripts.autonomous_engineering_state_lock import durable_state_lock; "
        f"lock=durable_state_lock(Path({str(state_dir)!r})); "
        "lock.__enter__(); time.sleep(1.0); lock.__exit__(None,None,None)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with durable_state_lock(state_dir, timeout_seconds=0.05):
                    pass
            except TimeoutError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("A second process acquired the durable-state lock while the owner was active.")
    finally:
        child.wait(timeout=10)

    with durable_state_lock(state_dir, timeout_seconds=1):
        pass


def test_durable_state_lock_is_released_after_owner_process_crashes(tmp_path: Path):
    state_dir = tmp_path / "crash-state"
    code = (
        "import os; "
        "from pathlib import Path; "
        "from scripts.autonomous_engineering_state_lock import durable_state_lock; "
        f"lock=durable_state_lock(Path({str(state_dir)!r})); "
        "lock.__enter__(); os._exit(29)"
    )
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert child.returncode == 29

    with durable_state_lock(state_dir, timeout_seconds=2):
        pass


@pytest.mark.skipif(os.name != "nt", reason="PowerShell/Win32 mutex interoperability is Windows-only")
def test_python_and_powershell_share_the_same_durable_state_mutex(tmp_path: Path):
    ready = tmp_path / "powershell-mutex-ready.txt"
    script = (
        f"$mutex = [System.Threading.Mutex]::new($false, '{MUTEX_NAME}'); "
        "$null = $mutex.WaitOne(); "
        f"[System.IO.File]::WriteAllText('{str(ready)}', 'ready'); "
        "Start-Sleep -Seconds 2; "
        "$mutex.ReleaseMutex(); $mutex.Dispose()"
    )
    child = subprocess.Popen(
        ["pwsh", "-NoProfile", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.is_file():
            if child.poll() is not None:
                output = child.stdout.read() if child.stdout else ""
                pytest.fail(f"PowerShell mutex owner exited early: {output}")
            time.sleep(0.05)
        assert ready.is_file(), "PowerShell did not acquire the shared mutex."

        with pytest.raises(TimeoutError):
            with durable_state_lock(timeout_seconds=0.1):
                pass
    finally:
        child.wait(timeout=10)

    with durable_state_lock(timeout_seconds=2):
        pass
