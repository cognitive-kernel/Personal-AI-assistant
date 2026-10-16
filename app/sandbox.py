"""Fail-closed, Docker-isolated Python execution primitive.

The sandbox is intentionally not registered as a task handler yet. It requires
an already available, digest-pinned official Python image and never pulls images
or enables network access during a run.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SandboxStatus = Literal["succeeded", "failed", "timed_out", "output_limit_exceeded", "unavailable"]


@dataclass(frozen=True)
class SandboxResult:
    status: SandboxStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    reason: str | None = None


_OFFICIAL_PYTHON_IMAGE = re.compile(
    r"^(?:python|docker\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)
_MAX_SOURCE_BYTES = 64 * 1024
_DEFAULT_MAX_OUTPUT_BYTES = 32 * 1024
_ALLOWED_DOCKER_ENV = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "DOCKER_API_VERSION",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
)


def _docker_environment() -> dict[str, str]:
    """Keep unrelated application secrets out of the Docker CLI process."""
    return {key: os.environ[key] for key in _ALLOWED_DOCKER_ENV if key in os.environ}


def _read_bounded(stream, output: bytearray, max_bytes: int, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = max_bytes - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _stop_process(process: subprocess.Popen) -> None:
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def run_python(
    source_code: str,
    *,
    docker_executable: str | Path,
    image: str,
    timeout_seconds: int = 10,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> SandboxResult:
    """Run one Python file in a bounded, no-network Docker container."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("source_code must be non-empty text.")
    if "\x00" in source_code:
        raise ValueError("source_code must not contain NUL characters.")
    if len(source_code.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise ValueError("source_code exceeds the 64 KiB limit.")
    if not isinstance(image, str) or not _OFFICIAL_PYTHON_IMAGE.fullmatch(image):
        raise ValueError("image must be a digest-pinned official Python image.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be an integer from 1 to 120.")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
        raise ValueError("max_output_bytes must be an integer from 1 to 65536.")

    executable = Path(docker_executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        return SandboxResult(
            status="unavailable",
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=0,
            reason="Docker executable is not available at the configured absolute path.",
        )

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="personal-ai-sandbox-") as directory:
        workspace = Path(directory).resolve()
        source_path = workspace / "main.py"
        source_path.write_text(source_code, encoding="utf-8", newline="\n")
        try:
            source_path.chmod(0o444)
            workspace.chmod(0o755)
        except OSError:
            # Docker's read-only bind mount remains the enforced container boundary.
            pass

        mount_source = str(workspace)
        if "," in mount_source:
            raise ValueError("Sandbox workspace path cannot contain commas.")
        command = [
            str(executable),
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=1",
            "--stop-timeout=1",
            "--user=65534:65534",
            "--workdir=/workspace",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            f"type=bind,source={mount_source},target=/workspace,readonly",
            image,
            "python",
            "-I",
            "-S",
            "/workspace/main.py",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                env=_docker_environment(),
            )
        except (FileNotFoundError, PermissionError, OSError):
            return SandboxResult(
                status="unavailable",
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=int((time.monotonic() - started) * 1000),
                reason="Docker could not be started; no local execution fallback is permitted.",
            )

        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        overflow = threading.Event()
        readers = [
            threading.Thread(
                target=_read_bounded,
                args=(process.stdout, stdout_buffer, max_output_bytes, overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded,
                args=(process.stderr, stderr_buffer, max_output_bytes, overflow),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        while process.poll() is None:
            if overflow.is_set():
                _stop_process(process)
                break
            if time.monotonic() - started >= timeout_seconds:
                timed_out = True
                _stop_process(process)
                break
            time.sleep(0.02)
        exit_code = process.wait()
        for reader in readers:
            reader.join(timeout=2)

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = stdout_buffer.decode("utf-8", errors="replace")
        stderr = stderr_buffer.decode("utf-8", errors="replace")
        if timed_out:
            return SandboxResult(
                "timed_out", exit_code, stdout, stderr, duration_ms,
                "Sandbox exceeded its execution time limit.",
            )
        if overflow.is_set():
            return SandboxResult(
                "output_limit_exceeded", exit_code, stdout, stderr, duration_ms,
                "Sandbox output exceeded the configured per-stream limit.",
            )
        if exit_code == 0:
            return SandboxResult("succeeded", exit_code, stdout, stderr, duration_ms)
        return SandboxResult("failed", exit_code, stdout, stderr, duration_ms)
