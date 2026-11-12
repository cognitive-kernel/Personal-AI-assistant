from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import autonomous_engineering_execution_lock as execution_lock
from scripts.autonomous_engineering_checkpoint_lock import checkpoint_store_lock
from scripts.autonomous_engineering_state_lock import durable_state_lock
from scripts.autonomous_engineering_pending_task_state import (
    claim_pending_task_owner_unlocked,
    pending_task_owner_path,
    task_digest,
    validate_pending_task_owner_unlocked,
)
from scripts import autonomous_engineering_pending_pr_state as pending_pr_state
from scripts.autonomous_engineering_checkpoint_process import (
    process_start_time,
    stop_owned_watcher,
)


def _start_child(command: list[str], *, cwd: Path = ROOT) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    line = process.stdout.readline().strip() if process.stdout else ""
    if line != "LOCKED":
        try:
            process.terminate()
        except OSError:
            pass
        try:
            _, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate(timeout=5)
        raise AssertionError(
            f"Child did not acquire its lock (stdout marker={line!r}, stderr={stderr[-2000:]!r})."
        )
    return process


def _stop_child(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class TargetRunnerArchitectureAcceptance(unittest.TestCase):
    def test_repository_execution_lock_is_exclusive_and_released_after_process_death(self):
        with tempfile.TemporaryDirectory(prefix="autonomous-execution-lock-") as temp:
            state_dir = Path(temp)
            child_code = (
                "import sys,time; from pathlib import Path; "
                "import scripts.autonomous_engineering_execution_lock as m; "
                "m.execution_state_directory=lambda:Path(sys.argv[1]); "
                "ctx=m.repository_execution_lock('target-child','1'); "
                "ctx.__enter__(); print('LOCKED',flush=True); time.sleep(120)"
            )
            child = _start_child([sys.executable, "-c", child_code, str(state_dir)])
            try:
                with mock.patch.object(execution_lock, "execution_state_directory", lambda: state_dir):
                    with self.assertRaisesRegex(RuntimeError, "Another autonomous execution owns"):
                        with execution_lock.repository_execution_lock("target-parent", "1"):
                            pass
                _stop_child(child)
                with mock.patch.object(execution_lock, "execution_state_directory", lambda: state_dir):
                    with execution_lock.repository_execution_lock("target-parent", "2"):
                        pass
            finally:
                _stop_child(child)

    def test_checkpoint_store_lock_is_exclusive_and_released_after_process_death(self):
        with tempfile.TemporaryDirectory(prefix="autonomous-checkpoint-lock-") as temp:
            state_dir = Path(temp)
            child_code = (
                "import sys,time; from pathlib import Path; "
                "from scripts.autonomous_engineering_checkpoint_lock import checkpoint_store_lock; "
                "ctx=checkpoint_store_lock(Path(sys.argv[1]),timeout_seconds=3); "
                "ctx.__enter__(); print('LOCKED',flush=True); time.sleep(120)"
            )
            child = _start_child([sys.executable, "-c", child_code, str(state_dir)])
            try:
                with self.assertRaisesRegex(TimeoutError, "checkpoint-store ownership"):
                    with checkpoint_store_lock(state_dir, timeout_seconds=0.2):
                        pass
                _stop_child(child)
                with checkpoint_store_lock(state_dir, timeout_seconds=2):
                    pass
            finally:
                _stop_child(child)

    def test_python_state_mutex_is_shared_with_powershell_and_recovers_abandoned_owner(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        self.assertIsNotNone(powershell, "Windows PowerShell 5.1 is required for cross-runtime mutex validation.")
        with tempfile.TemporaryDirectory(prefix="autonomous-state-mutex-") as temp:
            ps_code = (
                "$ErrorActionPreference='Stop'; "
                "$mutex=[System.Threading.Mutex]::new($false, "
                "'Local\\PersonalAIAssistant.AutonomousEngineering.State.v1'); "
                "if (-not $mutex.WaitOne(5000)) { throw 'mutex acquisition timed out' }; "
                "Write-Output 'LOCKED'; Start-Sleep -Seconds 120"
            )
            child = _start_child([powershell, "-NoProfile", "-Command", ps_code])
            try:
                with self.assertRaisesRegex(TimeoutError, "durable-state mutex"):
                    with durable_state_lock(Path(temp), timeout_seconds=0.2):
                        pass
                _stop_child(child)
                with durable_state_lock(Path(temp), timeout_seconds=2):
                    pass
            finally:
                _stop_child(child)

    def test_watcher_identity_stops_only_exact_process_and_rejects_reused_pid(self):
        with tempfile.TemporaryDirectory(prefix="autonomous-watcher-identity-") as temp:
            watcher = Path(temp) / "fake_watcher.py"
            watcher.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
            run_id = "target-host-acceptance"
            base_sha = "a" * 40
            command = [
                sys.executable,
                str(watcher),
                "watch",
                "--run-id",
                run_id,
                "--task",
                "1",
                "--base-sha",
                base_sha,
            ]

            exact = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.25)
                metadata = {
                    "watcher_pid": exact.pid,
                    "watcher_ready": True,
                    "run_id": run_id,
                    "task": 1,
                    "base_sha": base_sha,
                    "watcher_started_at": process_start_time(exact.pid),
                }
                stop_owned_watcher(metadata, script_path=watcher, timeout_seconds=3)
                exact.wait(timeout=5)
                self.assertIsNotNone(exact.poll())
            finally:
                _stop_child(exact)

            reused = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.25)
                metadata = {
                    "watcher_pid": reused.pid,
                    "watcher_ready": True,
                    "run_id": run_id,
                    "task": 1,
                    "base_sha": base_sha,
                    "watcher_started_at": process_start_time(reused.pid) + 1.0,
                }
                with self.assertRaisesRegex(RuntimeError, "creation time differs"):
                    stop_owned_watcher(metadata, script_path=watcher, timeout_seconds=1)
                self.assertIsNone(reused.poll(), "A PID-reused or ambiguous process must never be terminated.")
            finally:
                _stop_child(reused)

    def test_pending_task_reclaim_preserves_task_bytes_and_validates_owner(self):
        with tempfile.TemporaryDirectory(prefix="autonomous-task-owner-") as temp:
            state_dir = Path(temp)
            task = "target-runner acceptance task"
            task_path = state_dir / "pending-manual-task.txt"
            task_path.write_text(task, encoding="utf-8")
            with durable_state_lock(state_dir):
                first = claim_pending_task_owner_unlocked(
                    state_dir, task, "run-100", 1, source="manual_dispatch"
                )
                validate_pending_task_owner_unlocked(state_dir, task, "run-100", 1)
                second = claim_pending_task_owner_unlocked(
                    state_dir, task, "run-101", 1, source="recovery"
                )
                validated = validate_pending_task_owner_unlocked(state_dir, task, "run-101", 1)
            self.assertEqual(first["task_sha256"], task_digest(task))
            self.assertEqual(second["task_sha256"], first["task_sha256"])
            self.assertEqual(validated["owner_run_id"], "run-101")
            self.assertEqual(task_path.read_text(encoding="utf-8"), task)

            owner_path = pending_task_owner_path(state_dir)
            tampered = json.loads(owner_path.read_text(encoding="utf-8"))
            tampered["task_sha256"] = "0" * 64
            owner_path.write_text(json.dumps(tampered), encoding="utf-8")
            with durable_state_lock(state_dir):
                with self.assertRaisesRegex(RuntimeError, "does not match the task"):
                    validate_pending_task_owner_unlocked(state_dir, task, "run-101", 1)
            self.assertEqual(task_path.read_text(encoding="utf-8"), task)

    def test_pending_pr_cleanup_marker_recovers_interrupted_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="autonomous-pending-state-") as temp:
            state_dir = Path(temp)
            pr_path = state_dir / "pending-autonomous-pr.json"
            task_path = state_dir / "pending-manual-task.txt"
            owner_path = state_dir / "pending-manual-task-owner.json"
            marker_path = state_dir / "pending-state-cleanup.json"
            record = {
                "number": 1,
                "url": "https://github.com/cognitive-kernel/Personal-AI-assistant/pull/1",
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
                "branch": "codex/local-engineering-target-host",
                "run_id": "target-host-acceptance",
                "task": 1,
            }
            with mock.patch.object(pending_pr_state, "pending_pr_path", lambda: pr_path):
                pending_pr_state.save_pending_pr_unlocked(
                    record, "cognitive-kernel/Personal-AI-assistant"
                )
                self.assertEqual(pending_pr_state.load_pending_pr_unlocked(), record)
                task_path.write_text("preserve-until-verified-cleanup", encoding="utf-8")
                owner_path.write_text("owner-metadata", encoding="utf-8")
                pending_pr_state._write_cleanup_marker_unlocked(state_dir)
                pr_path.unlink()  # Simulate process death halfway through the accepted cleanup.
                self.assertTrue(pending_pr_state.recover_pending_state_cleanup_unlocked(state_dir))
            self.assertFalse(pr_path.exists())
            self.assertFalse(task_path.exists())
            self.assertFalse(owner_path.exists())
            self.assertFalse(marker_path.exists())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TargetRunnerArchitectureAcceptance)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
