from __future__ import annotations

import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


_MACHINE_ENVIRONMENT_KEY = (
    r"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment"
)


def _is_windows() -> bool:
    return os.name == "nt"


def _machine_configured_state_directory() -> str:
    """Read the machine-scoped state root; never trust a per-user override on Windows."""
    if not _is_windows():
        return ""
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - Windows standard library
        raise RuntimeError("Windows registry access is required to resolve shared state.") from exc
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENVIRONMENT_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AUTONOMOUS_ENGINEERING_PROGRESS_DIR")
            return str(value).strip()
    except FileNotFoundError:
        return ""


def execution_state_directory(
    *,
    is_windows_fn: Callable[[], bool] | None = None,
    machine_state_fn: Callable[[], str] | None = None,
) -> Path:
    """Resolve one stable state root shared by every participating process identity."""
    is_windows = is_windows_fn or _is_windows
    machine_state = machine_state_fn or _machine_configured_state_directory
    process_configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
    if is_windows():
        machine_configured = machine_state()
        if process_configured and not machine_configured:
            raise RuntimeError(
                "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process/user-scoped; configure it "
                "at machine scope so all Windows accounts share one state root."
            )
        if machine_configured:
            machine_path = os.path.expandvars(machine_configured).strip()
            if not Path(machine_path).is_absolute():
                raise ValueError(
                    "Machine AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute path."
                )
            if process_configured:
                process_path = os.path.expandvars(process_configured).strip()
                if not Path(process_path).is_absolute() or (
                    os.path.normcase(os.path.abspath(process_path))
                    != os.path.normcase(os.path.abspath(machine_path))
                ):
                    raise RuntimeError(
                        "Process AUTONOMOUS_ENGINEERING_PROGRESS_DIR differs from the "
                        "machine-scoped state root; refusing per-account state divergence."
                    )
            return Path(machine_path)

    if process_configured:
        expanded = Path(os.path.expandvars(process_configured)).expanduser()
        if not expanded.is_absolute():
            raise ValueError(
                "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute path so "
                "different working directories and Windows accounts resolve the same state."
            )
        return expanded

    program_data = os.getenv("PROGRAMDATA", "").strip()
    if program_data:
        shared_root = Path(os.path.expandvars(program_data)).expanduser()
        if not shared_root.is_absolute():
            raise RuntimeError("PROGRAMDATA must resolve to an absolute shared path.")
        return shared_root / "PersonalAIAssistant" / "autonomous-engineering"
    if is_windows():
        raise RuntimeError(
            "PROGRAMDATA is unavailable; refusing to fall back to a per-user "
            "autonomous-engineering state directory on Windows."
        )

    return Path.home() / ".personal-ai-assistant" / "autonomous-engineering"


def _validate_lock_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("File-lock timeout must be a finite non-negative number.")
    try:
        timeout = float(timeout_seconds)
    except (OverflowError, ValueError) as exc:
        raise ValueError("File-lock timeout must be a finite non-negative number.") from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("File-lock timeout must be a finite non-negative number.")
    return timeout


@contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float,
    timeout_message: str,
) -> Iterator[None]:
    """Acquire an OS-managed exclusive byte-zero lock, released on process exit."""
    timeout = _validate_lock_timeout(timeout_seconds)
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()

        if os.name == "nt":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(timeout_message) from exc
                    time.sleep(min(0.05, remaining))
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(timeout_message) from exc
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def durable_state_lock(
    state_dir: Path | None = None,
    *,
    timeout_seconds: float = 30.0,
    default_state_dir_fn: Callable[[], Path] | None = None,
) -> Iterator[None]:
    """Cross-process, cross-session lock shared by Python and PowerShell."""
    if state_dir is None:
        state_dir = default_state_dir_fn() if default_state_dir_fn else execution_state_directory()
    lock_path = Path(state_dir) / "autonomous-durable-state.lock"
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=timeout_seconds,
        timeout_message="Timed out waiting for the shared durable-state file lock.",
    ):
        yield
