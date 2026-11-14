from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.autonomous_engineering_state_lock import durable_state_lock


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


@pytest.mark.skipif(os.name != "nt", reason="PowerShell/Windows file-lock interoperability is Windows-only")
def test_python_and_powershell_share_the_same_durable_state_file_lock(tmp_path: Path):
    state_dir = tmp_path / "shared-state"
    state_dir.mkdir()
    lock_path = state_dir / "autonomous-durable-state.lock"
    ready = tmp_path / "powershell-lock-ready.txt"
    escaped_lock = str(lock_path).replace("'", "''")
    escaped_ready = str(ready).replace("'", "''")
    script = (
        "$path = '" + escaped_lock + "'; "
        "$stream = [System.IO.File]::Open($path, [System.IO.FileMode]::OpenOrCreate, "
        "[System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite); "
        "if ($stream.Length -eq 0) { $stream.WriteByte(0); $stream.Flush() }; "
        "$stream.Lock(0, 1); "
        "[System.IO.File]::WriteAllText('" + escaped_ready + "', 'ready'); "
        "Start-Sleep -Seconds 2; "
        "$stream.Unlock(0, 1); $stream.Dispose()"
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
                pytest.fail(f"PowerShell file-lock owner exited early: {output}")
            time.sleep(0.05)
        assert ready.is_file(), "PowerShell did not acquire the shared state file lock."

        with pytest.raises(TimeoutError):
            with durable_state_lock(state_dir, timeout_seconds=0.1):
                pass
    finally:
        child.wait(timeout=10)

    with durable_state_lock(state_dir, timeout_seconds=2):
        pass
