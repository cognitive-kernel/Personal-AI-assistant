import io
import subprocess

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
    assert "no local execution fallback" not in (result.reason or "").lower()


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
