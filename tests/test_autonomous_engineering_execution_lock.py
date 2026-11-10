from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.autonomous_engineering_execution_lock import (
    execution_state_directory,
    repository_execution_lock,
)


def test_execution_lock_records_owner_metadata_and_releases_after_scope(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
    with repository_execution_lock("run-1", "attempt-2"):
        pass

    # Windows byte-range locks also prevent a second handle from reading the
    # locked file; inspect its diagnostic metadata after ownership is released.
    import json

    metadata = json.loads((tmp_path / "repository-execution.lock").read_text(encoding="utf-8"))
    assert metadata["pid"] == os.getpid()
    assert metadata["run_id"] == "run-1"
    assert metadata["run_attempt"] == "attempt-2"
    assert metadata["host"]
    assert metadata["acquired_at"] > 0

    with repository_execution_lock("run-2", "attempt-1"):
        pass


def test_execution_lock_is_released_by_operating_system_after_process_crash(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(tmp_path))
    code = (
        "import os; "
        "from scripts.autonomous_engineering_execution_lock import repository_execution_lock; "
        "lock=repository_execution_lock('crashed-run','1'); "
        "lock.__enter__(); os._exit(23)"
    )
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert child.returncode == 23

    with repository_execution_lock("recovery-run", "1"):
        pass


def test_execution_state_directory_uses_configured_shared_root(monkeypatch, tmp_path: Path):
    configured = tmp_path / "shared-state"
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(configured))
    assert execution_state_directory() == configured


def test_cycle_orchestrator_preserves_public_lock_api_via_module_boundary():
    from scripts import autonomous_engineering_cycle as cycle

    assert cycle.repository_execution_lock is repository_execution_lock
