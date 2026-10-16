import io
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import sandbox


IMAGE = "python:3.12-slim@sha256:" + "a" * 64


class FakeProcess:
    def __init__(self, args, *, stdout=b"hello\n", stderr=b"", returncode=0):
        self.args = args
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def _configured_docker(tmp_path):
    executable = tmp_path / ("docker.exe" if __import__("os").name == "nt" else "docker")
    executable.write_text("placeholder", encoding="utf-8")
    return executable


def test_sandbox_runs_only_digest_pinned_python_in_no_network_container(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    calls = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(args)
        calls.append((args, kwargs, process))
        return process

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("KAGGLE_API_TOKEN", "must-not-enter-docker-cli-env")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    result = sandbox.run_python(
        "print('hello')",
        docker_executable=executable,
        image=IMAGE,
    )

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert len(calls) == 1
    args, kwargs, _ = calls[0]
    assert args[:2] == [str(executable), "run"]
    assert "--pull=never" in args
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--memory=256m" in args
    assert "--cpus=1" in args
    assert "--pids-limit=64" in args
    assert "--user=65534:65534" in args
    assert args[-4:] == ["python", "-I", "-S", "/workspace/main.py"]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert "KAGGLE_API_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"


def test_sandbox_returns_unavailable_without_docker_and_never_falls_back(tmp_path):
    result = sandbox.run_python(
        "print('should not run')",
        docker_executable=tmp_path / "missing-docker",
        image=IMAGE,
    )

    assert result.status == "unavailable"
    assert result.exit_code is None
    assert "docker executable is not available" in (result.reason or "").lower()


def test_sandbox_bounds_source_image_timeout_and_output():
    with pytest.raises(ValueError, match="non-empty"):
        sandbox.run_python("", docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="NUL"):
        sandbox.run_python("print('x')\x00", docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="64 KiB"):
        sandbox.run_python("x" * (64 * 1024 + 1), docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="digest-pinned"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image="python:3.12-slim")
    with pytest.raises(ValueError, match="timeout_seconds"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image=IMAGE, timeout_seconds=True)
    with pytest.raises(ValueError, match="max_output_bytes"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image=IMAGE, max_output_bytes=0)


def test_sandbox_caps_output_per_stream_and_reports_nonzero_exit(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    processes = iter(
        [
            FakeProcess([], stdout=b"123456789", stderr=b"", returncode=0),
            FakeProcess([], stdout=b"", stderr=b"traceback", returncode=2),
        ]
    )
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: next(processes))

    oversized = sandbox.run_python(
        "print('large')",
        docker_executable=executable,
        image=IMAGE,
        max_output_bytes=4,
    )
    failed = sandbox.run_python(
        "raise RuntimeError()",
        docker_executable=executable,
        image=IMAGE,
    )

    assert oversized.status == "output_limit_exceeded"
    assert oversized.stdout == "1234"
    assert failed.status == "failed"
    assert failed.exit_code == 2
    assert failed.stderr == "traceback"


def test_sandbox_terminates_container_client_after_timeout(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)
    times = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: next(times))

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        timeout_seconds=1,
    )

    assert result.status == "timed_out"
    assert process.terminated



def test_sandbox_removes_temporary_source_workspace_after_exit(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    captured = {}

    def fake_popen(args, **kwargs):
        captured["mount"] = args[args.index("--mount") + 1]
        return FakeProcess(args, stdout=b"done", returncode=0)

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_python(
        "print('done')",
        docker_executable=executable,
        image=IMAGE,
    )

    source = captured["mount"].split(",")[1].removeprefix("source=")
    assert result.status == "succeeded"
    assert not __import__("pathlib").Path(source).exists()



@pytest.mark.parametrize(
    "control,expected",
    [
        ("pause_requested", "paused"),
        ("cancel_requested", "cancelled"),
        ("unknown", "cancelled"),
    ],
)
def test_sandbox_stops_container_when_task_control_changes(tmp_path, monkeypatch, control, expected):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        control_check=lambda: control,
    )

    assert result.status == expected
    assert process.terminated
    assert "task" in (result.reason or "").lower() or "control" in (result.reason or "").lower()


def test_sandbox_stops_if_task_control_check_raises(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)

    def lost_lease():
        raise RuntimeError("lease lost")

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        control_check=lost_lease,
    )

    assert result.status == "cancelled"
    assert process.terminated
    assert "lease state was unavailable" in (result.reason or "")



def test_sandbox_container_is_labeled_with_task_id(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProcess(args)

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_python(
        "print('task')",
        docker_executable=executable,
        image=IMAGE,
        task_id=41,
    )

    assert result.status == "succeeded"
    assert "--label" in captured["args"]
    assert f"{sandbox._SANDBOX_LABEL}=true" in captured["args"]
    assert f"{sandbox._TASK_ID_LABEL}=41" in captured["args"]


def test_cleanup_removes_only_old_containers_for_confirmed_terminal_tasks(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    container_ids = ["a" * 12, "b" * 12, "c" * 12, "d" * 12]
    metadata = {
        container_ids[0]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "7"}, "running", old),
        container_ids[1]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "8"}, "running", old),
        container_ids[2]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "9"}, "exited", recent),
        container_ids[3]: ({sandbox._SANDBOX_LABEL: "false", sandbox._TASK_ID_LABEL: "7"}, "exited", old),
    }
    removed = []

    def fake_run(args, **kwargs):
        if args[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(container_ids), "")
        if args[1] == "inspect":
            labels, state, created = metadata[args[-1]]
            return subprocess.CompletedProcess(
                args, 0, f"{json.dumps(labels)}|{state}|{created}", ""
            )
        if args[1] == "rm":
            removed.append(args[-1])
            return subprocess.CompletedProcess(args, 0, args[-1], "")
        raise AssertionError(f"Unexpected Docker command: {args[1:]}")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = sandbox.cleanup_orphaned_containers(
        docker_executable=executable,
        terminal_task_ids={7, 9},
        min_age_seconds=60,
    )

    assert result.removed == (container_ids[0],)
    assert result.skipped == 3
    assert result.errors == ()
    assert removed == [container_ids[0]]


def test_cleanup_fails_closed_when_docker_listing_fails(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "daemon unavailable")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = sandbox.cleanup_orphaned_containers(
        docker_executable=executable,
        terminal_task_ids={1},
    )

    assert result.removed == ()
    assert result.errors == ("Docker could not list sandbox containers.",)


def test_cleanup_rejects_invalid_task_ids_before_running_docker(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    called = []
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *args, **kwargs: called.append(args))

    with pytest.raises(ValueError, match="positive integer"):
        sandbox.cleanup_orphaned_containers(
            docker_executable=executable,
            terminal_task_ids={True},
        )
    assert called == []
