from __future__ import annotations

import json
import math
import os
import shutil
import time
from contextlib import ExitStack, contextmanager
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


def _is_path_within(path: Path, parent: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _shared_site_state_directory() -> Path:
    """Return the OS-wide application data root, never a per-user home directory."""
    try:
        from platformdirs import site_data_path
    except ImportError as exc:  # pragma: no cover - dependency installation failure
        raise RuntimeError(
            "The platformdirs runtime dependency is missing; install the application dependencies."
        ) from exc
    root = Path(site_data_path("PersonalAIAssistant", appauthor=False)) / "autonomous-engineering"
    if not root.is_absolute():
        raise RuntimeError("The platform shared-data directory did not resolve to an absolute path.")
    if _is_path_within(root, Path.home()):
        raise RuntimeError(
            "The platform shared-data directory resolves inside the current user's home; "
            "configure AUTONOMOUS_ENGINEERING_PROGRESS_DIR to one shared system path."
        )
    return root


def _migrate_legacy_posix_state(legacy: Path, shared: Path) -> None:
    """Copy legacy per-user state into the shared root without deleting either copy."""
    if not legacy.exists():
        return
    if legacy.is_symlink() or not legacy.is_dir():
        raise RuntimeError("The legacy autonomous-engineering state path is not a safe directory.")
    marker = legacy / ".shared-state-migration.json"
    if marker.is_symlink():
        raise RuntimeError("The legacy shared-state migration marker is not a regular file.")
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("The legacy shared-state migration marker is unreadable.") from exc
        if isinstance(saved, dict) and saved.get("target") == str(shared):
            return
        raise RuntimeError("The legacy state was previously migrated to a different shared root.")

    shared.mkdir(parents=True, exist_ok=True)
    if shared.is_symlink():
        raise RuntimeError("The shared autonomous-engineering state root cannot be a symbolic link.")

    lock_names = (
        "autonomous-durable-state.lock",
        "repository-execution.lock",
        "autonomous-checkpoint-store.lock",
    )
    lock_paths = sorted(
        [legacy / name for name in lock_names] + [shared / name for name in lock_names],
        key=lambda item: str(item.resolve()),
    )
    with ExitStack() as stack:
        for lock_path in lock_paths:
            stack.enter_context(exclusive_file_lock(
                lock_path,
                timeout_seconds=30,
                timeout_message="Timed out waiting to migrate legacy autonomous-engineering state.",
            ))

        sources = []
        conflicts = []
        for source in sorted(legacy.rglob("*")):
            if source.is_symlink():
                raise RuntimeError("Legacy autonomous-engineering state contains a symbolic link; migration stopped safely.")
            if not source.is_file() or source == marker:
                continue
            relative = source.relative_to(legacy)
            destination = shared / relative
            cursor = destination.parent
            while cursor != shared and shared in cursor.parents:
                if cursor.is_symlink():
                    raise RuntimeError("The shared state tree contains a symbolic link; migration stopped safely.")
                cursor = cursor.parent
            if destination.is_symlink():
                raise RuntimeError("A shared-state migration target is a symbolic link.")
            if destination.exists():
                if not destination.is_file() or source.read_bytes() != destination.read_bytes():
                    conflicts.append(relative.as_posix())
            else:
                sources.append((source, destination))

        if conflicts:
            sample = ", ".join(conflicts[:8])
            raise RuntimeError(
                "Legacy and shared autonomous-engineering state contain conflicting files; "
                f"both copies were preserved. Reconcile these paths before continuing: {sample}"
            )

        for source, destination in sources:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".migration.tmp")
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

        temporary_marker = marker.with_name(marker.name + ".tmp")
        temporary_marker.write_text(
            json.dumps({"schema_version": 1, "target": str(shared), "migrated_at": time.time()}, indent=2)
            + "\\n",
            encoding="utf-8",
        )
        os.replace(temporary_marker, marker)


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
        require_machine_scope = (
            os.getenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", "").strip().lower()
            == "true"
        )
        if process_configured and not machine_configured and require_machine_scope:
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
                "different working directories and service identities resolve the same state."
            )
        if not is_windows():
            if _is_path_within(expanded, Path.home()):
                raise ValueError(
                    "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must not be inside a per-user home directory; "
                    "configure one shared system path for all participating services."
                )
            _migrate_legacy_posix_state(
                Path.home() / ".personal-ai-assistant" / "autonomous-engineering",
                expanded,
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

    shared_root = _shared_site_state_directory()
    _migrate_legacy_posix_state(
        Path.home() / ".personal-ai-assistant" / "autonomous-engineering",
        shared_root,
    )
    return shared_root


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
