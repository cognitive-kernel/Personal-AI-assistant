from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from scripts import autonomous_engineering_cycle_checkpoints as checkpoints


def test_posix_checkpoint_watcher_uses_a_new_session_and_shared_state(tmp_path: Path):
    state_dir = tmp_path / "shared-state"
    state_dir.mkdir()
    captured = {}

    class FakeProcess:
        pid = 4321

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            metadata = {
                "schema_version": 4,
                "base_sha": "a" * 40,
                "run_id": "posix-run",
                "task": 1,
                "watcher_pid": self.pid,
                "watcher_started_at": 1234.5,
                "watcher_ready": True,
            }
            (state_dir / "worktree-checkpoint-posix-run-1.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )

        def poll(self):
            return None

    inherited_pythonpath = str(tmp_path / "ambient-modules")
    purelib = str(tmp_path / ".venv" / "site-packages")
    platlib = str(tmp_path / ".venv" / "native-site-packages")
    os_module = SimpleNamespace(
        name="posix",
        environ={"PYTHONPATH": inherited_pythonpath},
        path=os.path,
        pathsep=os.pathsep,
    )
    sys_module = SimpleNamespace(executable=str(tmp_path / "python"))
    sysconfig_module = SimpleNamespace(
        get_paths=lambda: {"purelib": purelib, "platlib": platlib}
    )
    time_module = SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _seconds: None)

    process = checkpoints.start_checkpoint_watcher(
        "a" * 40,
        "posix-run",
        1,
        os_module=os_module,
        subprocess_module=SimpleNamespace(
            Popen=FakeProcess,
            DEVNULL=subprocess.DEVNULL,
            STDOUT=subprocess.STDOUT,
            DETACHED_PROCESS=0,
            CREATE_NEW_PROCESS_GROUP=0,
        ),
        sys_module=sys_module,
        sysconfig_module=sysconfig_module,
        time_module=time_module,
        root=tmp_path,
        checkpoint_script=tmp_path / "checkpoint.py",
        execution_state_directory_fn=lambda: state_dir,
        process_start_time_fn=lambda _pid: 1234.5,
        metadata_confirms_watcher_ready_fn=lambda metadata, **expected: (
            metadata["watcher_pid"] == expected["watcher_pid"]
            and metadata["watcher_started_at"] == expected["watcher_started_at"]
            and metadata["base_sha"] == expected["base_sha"]
            and metadata["run_id"] == expected["run_id"]
            and metadata["task"] == expected["task"]
        ),
        programdata=None,
    )

    assert process is not None
    assert captured["command"][0] == str(tmp_path / "python")
    assert captured["kwargs"]["start_new_session"] is True
    assert "creationflags" not in captured["kwargs"]
    pythonpath = captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)
    assert pythonpath == [
        str(tmp_path),
        purelib,
        platlib,
        inherited_pythonpath,
    ]
    assert captured["kwargs"]["cwd"] == tmp_path


def test_posix_checkpoint_restore_and_clear_are_not_disabled_without_programdata():
    calls = []

    def command(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="restored")

    assert checkpoints.restore_checkpoint(
        "a" * 40,
        os_name="posix",
        programdata=None,
        checkpoint_command=command,
    ) is True
    checkpoints.clear_checkpoint(
        "posix-run",
        1,
        os_name="posix",
        programdata=None,
        checkpoint_command=command,
    )

    assert calls == [
        ("restore", "--base-sha", "a" * 40),
        ("clear", "--run-id", "posix-run", "--task", "1"),
    ]


def test_posix_checkpoint_stop_terminates_the_owned_process_group(monkeypatch):
    import signal

    killed = []

    class LiveWatcher:
        pid = 8765

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        checkpoints.os,
        "killpg",
        lambda process_group, sig: killed.append((process_group, sig)),
        raising=False,
    )
    checkpoints.stop_checkpoint_watcher(
        LiveWatcher(),
        "a" * 40,
        "posix-run",
        1,
        subprocess_module=SimpleNamespace(TimeoutExpired=subprocess.TimeoutExpired),
        os_name="posix",
        programdata=None,
        checkpoint_command=lambda *_args: SimpleNamespace(returncode=0, stdout=""),
    )

    assert killed == [(8765, signal.SIGTERM)]


def test_posix_checkpoint_stop_saves_final_snapshot_when_watcher_exists():
    calls = []

    class FakeWatcher:
        def poll(self):
            return 0

    def command(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="")

    checkpoints.stop_checkpoint_watcher(
        FakeWatcher(),
        "a" * 40,
        "posix-run",
        1,
        subprocess_module=SimpleNamespace(TimeoutExpired=subprocess.TimeoutExpired),
        os_name="posix",
        programdata=None,
        checkpoint_command=command,
    )

    assert calls == [
        ("stop", "--run-id", "posix-run", "--task", "1"),
        ("snapshot", "--base-sha", "a" * 40, "--run-id", "posix-run", "--task", "1"),
    ]
