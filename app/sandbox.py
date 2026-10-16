"""Fail-closed, Docker-isolated Python execution primitive.

The sandbox is intentionally not registered as a task handler yet. It requires
an already available, digest-pinned official Python image and never pulls images
or enables network access during a run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal


SandboxStatus = Literal["succeeded", "failed", "timed_out", "paused", "cancelled", "output_limit_exceeded", "unavailable"]

_SANDBOX_LABEL = "com.cognitivekernel.personal-ai-assistant.sandbox"
_TASK_ID_LABEL = "com.cognitivekernel.personal-ai-assistant.task-id"
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


@dataclass(frozen=True)
class SandboxResult:
    status: SandboxStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    reason: str | None = None


@dataclass(frozen=True)
class SandboxCleanupResult:
    removed: tuple[str, ...]
    skipped: int
    errors: tuple[str, ...]


def run_workspace_syntax_check(
    workspace_path: str | Path,
    *,
    docker_executable: str | Path,
    image: str,
    timeout_seconds: int = 60,
    max_output_bytes: int = 16384,
) -> SandboxResult:
    """Parse Python files in a read-only project workspace inside Docker.

    Only a fixed AST parser runs in the container; candidate project code is
    read as text and never imported or executed. This is a syntax check, not a
    test suite or security proof.
    """
    workspace_value = Path(workspace_path).expanduser()
    if not workspace_value.is_absolute():
        raise ValueError("workspace_path must be an absolute path.")
    try:
        workspace = workspace_value.resolve(strict=True)
    except OSError as exc:
        raise ValueError("workspace_path must exist.") from exc
    if not workspace.is_dir() or not (workspace / "app").is_dir() or not (workspace / "tests").is_dir():
        raise ValueError("workspace_path must contain app/ and tests/ directories.")
    if "," in str(workspace):
        raise ValueError("Workspace path cannot contain commas.")
    if not isinstance(image, str) or not _OFFICIAL_PYTHON_IMAGE.fullmatch(image):
        raise ValueError("image must be a digest-pinned official Python image.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be an integer from 1 to 120.")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
        raise ValueError("max_output_bytes must be an integer from 1 to 65536.")

    executable = Path(docker_executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        return SandboxResult(
            status="unavailable", exit_code=None, stdout="", stderr="", duration_ms=0,
            reason="Docker executable is not available at the configured absolute path.",
        )

    parser = (
        "import ast,json,pathlib,sys\n"
        "root=pathlib.Path('/workspace')\n"
        "files=[]\n"
        "for folder in ('app','tests'):\n"
        " base=root/folder\n"
        " for path in base.rglob('*.py'):\n"
        "  if path.is_symlink(): continue\n"
        "  if len(files)>=5000: print(json.dumps({'status':'failed','reason':'file_limit_exceeded'})); sys.exit(2)\n"
        "  files.append(path)\n"
        "for path in sorted(files):\n"
        " try:\n"
        "  if path.stat().st_size>2097152: raise ValueError('file_size_limit_exceeded')\n"
        "  ast.parse(path.read_text(encoding='utf-8'), filename=str(path.relative_to(root)))\n"
        " except Exception as exc:\n"
        "  print(json.dumps({'status':'failed','file':str(path.relative_to(root)),'reason':type(exc).__name__,'detail':str(exc)[:300]}))\n"
        "  sys.exit(2)\n"
        "print(json.dumps({'status':'passed','files_checked':len(files)}))\n"
    )
    command = [
        str(executable), "run", "--rm", "--pull=never",
        "--label", f"{_SANDBOX_LABEL}=true",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=64",
        "--memory=256m", "--memory-swap=256m", "--cpus=1",
        "--stop-timeout=1", "--user=65534:65534",
        "--workdir=/workspace",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--mount", f"type=bind,source={str(workspace)},target=/workspace,readonly",
        image, "python", "-I", "-S", "-c", parser,
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, close_fds=True, env=_docker_environment(),
        )
    except (FileNotFoundError, PermissionError, OSError):
        return SandboxResult(
            status="unavailable", exit_code=None, stdout="", stderr="",
            duration_ms=int((time.monotonic() - started) * 1000),
            reason="Docker could not be started; no host execution fallback is permitted.",
        )

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    overflow = threading.Event()
    readers = [
        threading.Thread(target=_read_bounded, args=(process.stdout, stdout_buffer, max_output_bytes, overflow), daemon=True),
        threading.Thread(target=_read_bounded, args=(process.stderr, stderr_buffer, max_output_bytes, overflow), daemon=True),
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
        return SandboxResult("timed_out", exit_code, stdout, stderr, duration_ms, "Syntax check exceeded its execution time limit.")
    if overflow.is_set():
        return SandboxResult("output_limit_exceeded", exit_code, stdout, stderr, duration_ms, "Syntax check output exceeded its configured limit.")
    if exit_code == 0:
        return SandboxResult("succeeded", exit_code, stdout, stderr, duration_ms)
    return SandboxResult("failed", exit_code, stdout, stderr, duration_ms)


def cleanup_orphaned_containers(
    *,
    docker_executable: str | Path,
    terminal_task_ids: set[int] | frozenset[int],
    min_age_seconds: int = 60,
) -> SandboxCleanupResult:
    """Remove only old, app-labelled containers for tasks already confirmed terminal.

    The caller must obtain terminal_task_ids from the durable task store. Unknown
    state, active task IDs, malformed Docker output, and recent containers fail closed.
    This function is explicit and is not run automatically during application startup.
    """
    if not isinstance(terminal_task_ids, (set, frozenset)) or any(
        isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1
        for task_id in terminal_task_ids
    ):
        raise ValueError("terminal_task_ids must contain positive integer task IDs.")
    if isinstance(min_age_seconds, bool) or not isinstance(min_age_seconds, int) or min_age_seconds < 0:
        raise ValueError("min_age_seconds must be a non-negative integer.")
    executable = Path(docker_executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        return SandboxCleanupResult((), 0, ("Docker executable is not available at the configured absolute path.",))

    env = _docker_environment()
    try:
        listing = subprocess.run(
            [str(executable), "ps", "-aq", "--filter", f"label={_SANDBOX_LABEL}=true"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, shell=False,
            close_fds=True, env=env, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SandboxCleanupResult((), 0, (f"Could not list sandbox containers: {type(exc).__name__}.",))
    if listing.returncode != 0:
        return SandboxCleanupResult((), 0, ("Docker could not list sandbox containers.",))

    all_container_ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if any(not _CONTAINER_ID.fullmatch(container_id) for container_id in all_container_ids):
        return SandboxCleanupResult((), 0, ("Docker returned an invalid container ID; cleanup aborted.",))
    # Bound each pass so an unexpectedly large Docker inventory cannot stall a worker.
    container_ids = all_container_ids[:100]

    removed: list[str] = []
    skipped = max(0, len(all_container_ids) - len(container_ids))
    errors: list[str] = []
    now = datetime.now(timezone.utc)
    for container_id in container_ids:
        try:
            inspected = subprocess.run(
                [
                    str(executable), "inspect",
                    "--format={{json .Config.Labels}}|{{.State.Status}}|{{.Created}}",
                    container_id,
                ],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, shell=False,
                close_fds=True, env=env, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"Inspect failed for {container_id[:12]}: {type(exc).__name__}.")
            continue
        if inspected.returncode != 0:
            errors.append(f"Inspect failed for {container_id[:12]}.")
            continue
        try:
            labels_json, state, created_text = inspected.stdout.strip().split("|", 2)
            labels = json.loads(labels_json)
            task_id = int(labels.get(_TASK_ID_LABEL, ""))
            created = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError("created timestamp lacks timezone")
        except (ValueError, TypeError, json.JSONDecodeError):
            skipped += 1
            continue
        if labels.get(_SANDBOX_LABEL) != "true" or task_id not in terminal_task_ids:
            skipped += 1
            continue
        if state not in {"running", "paused", "exited", "dead", "created"}:
            skipped += 1
            continue
        age_seconds = (now - created.astimezone(timezone.utc)).total_seconds()
        if age_seconds < min_age_seconds:
            skipped += 1
            continue
        try:
            result = subprocess.run(
                [str(executable), "rm", "--force", container_id],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, shell=False,
                close_fds=True, env=env, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"Removal failed for {container_id[:12]}: {type(exc).__name__}.")
            continue
        if result.returncode == 0:
            removed.append(container_id)
        else:
            errors.append(f"Removal failed for {container_id[:12]}.")
    return SandboxCleanupResult(tuple(removed), skipped, tuple(errors))


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
    control_check: Callable[[], str] | None = None,
    task_id: int | None = None,
) -> SandboxResult:
    """Run one Python file in a bounded, no-network Docker container."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("source_code must be non-empty text.")
    if "\x00" in source_code:
        raise ValueError("source_code must not contain NUL characters.")
    if len(source_code.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise ValueError("source_code exceeds the 64 KiB limit.")
    if task_id is not None and (
        isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1
    ):
        raise ValueError("task_id must be a positive integer when provided.")
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
            "--label",
            f"{_SANDBOX_LABEL}=true",
            *(["--label", f"{_TASK_ID_LABEL}={task_id}"] if task_id is not None else []),
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
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534",
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
        interrupted_status: str | None = None
        interruption_reason: str | None = None
        while process.poll() is None:
            if control_check is not None:
                try:
                    control = control_check()
                except Exception:
                    control = "control_unavailable"
                if control in {"pause_requested", "paused"}:
                    interrupted_status = "paused"
                    interruption_reason = "Sandbox stopped at a task pause request."
                elif control in {"cancel_requested", "cancelled"}:
                    interrupted_status = "cancelled"
                    interruption_reason = "Sandbox stopped at a task cancellation request."
                elif control != "running":
                    interrupted_status = "cancelled"
                    interruption_reason = "Sandbox stopped because task control/lease state was unavailable."
                if interrupted_status is not None:
                    _stop_process(process)
                    break
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
        if interrupted_status is not None:
            return SandboxResult(
                interrupted_status, exit_code, stdout, stderr, duration_ms,
                interruption_reason,
            )
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
