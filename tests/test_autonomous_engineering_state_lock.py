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


def test_durable_state_lock_uses_a_session_independent_file_contract():
    root = Path(__file__).resolve().parents[1]
    python_lock = (root / "scripts" / "autonomous_engineering_state_lock.py").read_text(encoding="utf-8")
    powershell_persistence = (root / "scripts" / "persist_autonomous_manual_task.ps1").read_text(encoding="utf-8")
    assert "Local\\" not in python_lock
    assert "CreateMutexW" not in python_lock
    assert "autonomous-durable-state.lock" in python_lock
    assert "autonomous-durable-state.lock" in powershell_persistence
    assert ".Lock(0, 1)" in powershell_persistence
    assert ".Unlock(0, 1)" in powershell_persistence


def test_configured_state_root_must_be_absolute(monkeypatch, tmp_path: Path):
    from scripts import autonomous_engineering_state_paths as paths

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "relative-state")
    with pytest.raises(ValueError, match="absolute path"):
        paths.execution_state_directory()


def test_windows_state_root_never_falls_back_to_a_user_profile(monkeypatch):
    from scripts import autonomous_engineering_state_paths as paths

    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    _install_fake_machine_environment(monkeypatch, None)
    with pytest.raises(RuntimeError, match="PROGRAMDATA"):
        paths.execution_state_directory()



def _install_fake_machine_environment(monkeypatch, value: str | None):
    import sys
    from types import SimpleNamespace

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _open_key(_root, _path):
        if value is None:
            raise FileNotFoundError
        return _Key()

    def _query_value(_key, name):
        assert name == "AUTONOMOUS_ENGINEERING_PROGRESS_DIR"
        if value is None:
            raise FileNotFoundError
        return value, 1

    monkeypatch.setitem(
        sys.modules,
        "winreg",
        SimpleNamespace(
            HKEY_LOCAL_MACHINE=object(),
            OpenKey=_open_key,
            QueryValueEx=_query_value,
        ),
    )


def test_windows_state_root_uses_machine_scope_when_process_override_is_absent(
    monkeypatch, tmp_path: Path
):
    from scripts import autonomous_engineering_state_paths as paths

    machine_root = tmp_path / "machine-state"
    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "program-data"))
    _install_fake_machine_environment(monkeypatch, str(machine_root))

    assert paths.execution_state_directory() == machine_root


def test_windows_state_root_rejects_per_user_override_that_diverges_from_machine(
    monkeypatch, tmp_path: Path
):
    from scripts import autonomous_engineering_state_paths as paths

    machine_root = tmp_path / "machine-state"
    user_root = tmp_path / "user-state"
    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(user_root))
    _install_fake_machine_environment(monkeypatch, str(machine_root))

    with pytest.raises(RuntimeError, match="differs from the machine-scoped state root"):
        paths.execution_state_directory()


def test_windows_state_root_rejects_process_only_configuration(
    monkeypatch, tmp_path: Path
):
    from scripts import autonomous_engineering_state_paths as paths

    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path / "user-only"))
    _install_fake_machine_environment(monkeypatch, None)

    with pytest.raises(RuntimeError, match="process/user-scoped"):
        paths.execution_state_directory()


def test_powershell_manual_task_persistence_rejects_user_scoped_state_override():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "persist_autonomous_manual_task.ps1").read_text(encoding="utf-8")

    assert '[Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")' in script
    assert "process/user-scoped" in script
    assert "differs from the machine-scoped state root" in script
    assert "[System.IO.Path]::IsPathRooted($expandedStateDir)" in script
