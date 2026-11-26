#!/usr/bin/env python3
"""Idempotently install the POSIX machine state broker using systemd or launchd.

The service identity is derived from the shared state directory owner. No fixed
login name, primary group, repository checkout path, or user home is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import pwd
import shutil
import stat
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


SERVICE_NAME = "personal-ai-assistant-state-broker"
LAUNCHD_LABEL = "com.personal-ai-assistant.state-broker"


def _run(command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def _assert_no_active_runner_worker(system: str) -> None:
    ps = shutil.which("ps")
    if not ps:
        raise RuntimeError("Cannot safely verify whether a GitHub Runner job is active; the ps utility is unavailable.")
    arguments = ["-axo", "pid=,command="] if system == "darwin" else ["-eo", "pid=,args="]
    result = _run([ps, *arguments], timeout=10)
    if result.returncode != 0:
        raise RuntimeError("Cannot safely inspect running processes before state ACL or service changes.")
    import re
    worker = re.compile(r"(?i)(?:^|[\\s/])Runner\\.Worker(?:\\.dll|\\.exe)?(?:[\\s]|$)")
    active = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid != os.getpid() and worker.search(fields[1]):
            active.append(pid)
    if active:
        raise RuntimeError(
            "A GitHub Runner worker is currently active. Broker setup did not change state files, ACLs, or services; retry after the job completes."
        )


def _resolve_service_uid(raw_identity: str, *, source: str) -> int:
    value = (raw_identity or "").strip()
    if not value:
        raise RuntimeError(f"{source} did not publish a dedicated non-root Runner identity.")
    try:
        uid = int(value)
    except ValueError:
        try:
            uid = pwd.getpwnam(value).pw_uid
        except KeyError as exc:
            raise RuntimeError(f"{source} references an unknown local service identity.") from exc
    if uid <= 0:
        raise RuntimeError(f"{source} runs as root; a non-root Runner service identity is required.")
    return uid


def _discover_runner_service_uid(system: str) -> int | None:
    """Discover the installed GitHub Runner service identity without fixed names."""
    identities: set[int] = set()
    if system == "linux":
        systemctl = shutil.which("systemctl")
        if not systemctl or not Path("/run/systemd/system").is_dir():
            return None
        listed = _run([systemctl, "list-units", "--all", "--type=service", "--no-legend"], timeout=20)
        if listed.returncode != 0:
            raise RuntimeError("Could not inspect systemd services to identify the installed GitHub Runner.")
        units = []
        for line in listed.stdout.splitlines():
            fields = line.split()
            if fields and fields[0].startswith("actions.runner.") and fields[0].endswith(".service"):
                units.append(fields[0])
        for unit in sorted(set(units)):
            shown = _run([systemctl, "show", unit, "--property=User", "--value"], timeout=10)
            if shown.returncode != 0:
                raise RuntimeError(f"Could not read the service identity for {unit}.")
            identities.add(_resolve_service_uid(shown.stdout.strip(), source=unit))
    elif system == "darwin":
        launch_daemons = Path("/Library/LaunchDaemons")
        if not launch_daemons.is_dir():
            return None
        for plist_path in sorted(launch_daemons.glob("actions.runner.*.plist")):
            if plist_path.is_symlink() or not plist_path.is_file():
                raise RuntimeError(f"Unsafe GitHub Runner launchd registration: {plist_path.name}")
            try:
                with plist_path.open("rb") as stream:
                    plist = plistlib.load(stream)
            except (OSError, plistlib.InvalidFileException, ValueError) as exc:
                raise RuntimeError(f"Could not inspect GitHub Runner launchd registration {plist_path.name}.") from exc
            if not isinstance(plist, dict):
                raise RuntimeError(f"Invalid GitHub Runner launchd registration {plist_path.name}.")
            identities.add(_resolve_service_uid(str(plist.get("UserName", "")), source=plist_path.name))
    if len(identities) > 1:
        raise RuntimeError(
            "Multiple GitHub Runner services use different identities. "
            "Automatic shared-state ownership is ambiguous; configure one Runner service identity first."
        )
    return next(iter(identities)) if identities else None


def _path_is_secure_executable(path: Path, service_uid: int) -> bool:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        return False
    if info.st_uid not in {0, service_uid}:
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    for parent in resolved.parents:
        try:
            parent_info = parent.stat()
        except OSError:
            return False
        if parent_info.st_uid not in {0, service_uid}:
            return False
        if parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
        if parent == parent.parent:
            break
    return True


def _select_service_python(service_uid: int, suggested: str) -> Path:
    # Prefer OS-managed interpreter locations. A venv below /home or /Users is
    # intentionally rejected: systemd/launchd must not depend on a login home.
    candidates = [
        Path("/usr/bin/python3"),
        Path("/usr/local/bin/python3"),
        Path("/opt/homebrew/bin/python3"),
        Path("/opt/local/bin/python3"),
        Path(suggested),
    ]
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    home_roots = (Path("/home"), Path("/Users"))
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        if any(resolved == root or root in resolved.parents for root in home_roots):
            continue
        if _path_is_secure_executable(resolved, service_uid):
            try:
                version = subprocess.run(
                    [str(resolved), "-I", "-S", "-c", "import sys; print(sys.version_info.major * 100 + sys.version_info.minor)"],
                    capture_output=True, text=True, timeout=5, check=False, shell=False,
                )
                if version.returncode == 0 and int(version.stdout.strip()) >= 310:
                    return resolved
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
    raise RuntimeError(
        "No trusted machine-managed Python 3.10+ runtime is available for the POSIX broker. "
        "Install Python through the operating-system package manager, then restart the app. "
        "A user-home virtual environment is never executed by the machine service."
    )


def _write_atomic(path: Path, payload: bytes, mode: int, owner_uid: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError(f"Refusing a symlinked machine configuration directory: {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".paa-state-broker-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, owner_uid, -1)
        os.replace(temporary, path)
        os.chmod(path, mode)
        os.chown(path, owner_uid, -1)
    finally:
        temporary.unlink(missing_ok=True)


def _acquire_lock(path: Path, *, owner_uid: int | None = None, owner_gid: int | None = None):
    import fcntl
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if owner_uid is not None:
        os.fchown(descriptor, owner_uid, owner_gid if owner_gid is not None else -1)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\\0")
        os.fsync(descriptor)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _migrate_legacy_state(state_dir: Path, legacy_home: Path | None, service_uid: int) -> None:
    if legacy_home is None:
        return
    legacy = legacy_home / ".personal-ai-assistant" / "autonomous-engineering"
    if not legacy.exists():
        return
    if legacy.is_symlink() or not legacy.is_dir():
        raise RuntimeError("Legacy POSIX state path is not a safe directory; both locations are preserved.")
    state_dir.mkdir(parents=True, exist_ok=True)
    if state_dir.is_symlink():
        raise RuntimeError("Shared POSIX state root cannot be a symbolic link.")
    digest = hashlib.sha256(str(legacy.resolve()).encode("utf-8")).hexdigest()[:20]
    marker = state_dir / f".legacy-migration-{digest}.json"
    if marker.is_symlink():
        raise RuntimeError("Legacy migration marker is a symbolic link.")
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Legacy migration marker is unreadable.") from exc
        if isinstance(saved, dict) and saved.get("target") == str(state_dir):
            return
        raise RuntimeError("Legacy state was previously migrated to another target.")

    lock_names = (
        "repository-execution.lock",
        "autonomous-durable-state.lock",
        "autonomous-checkpoint-store.lock",
    )
    roots = sorted((legacy, state_dir), key=lambda value: str(value.resolve()))
    lock_paths = [root / name for name in lock_names for root in roots]
    descriptors = []
    try:
        for lock_path in lock_paths:
            is_shared_lock = lock_path.parent == state_dir or state_dir in lock_path.parents
            descriptors.append(_acquire_lock(
                lock_path,
                owner_uid=service_uid if is_shared_lock else None,
                owner_gid=pwd.getpwuid(service_uid).pw_gid if is_shared_lock else None,
            ))
        sources = []
        conflicts = []
        for root, directories, files in os.walk(legacy, followlinks=False):
            root_path = Path(root)
            for name in list(directories):
                path = root_path / name
                if path.is_symlink():
                    raise RuntimeError("Legacy state contains a symlink; migration stopped without changing either copy.")
            for name in files:
                source = root_path / name
                if source.is_symlink():
                    raise RuntimeError("Legacy state contains a symlink; migration stopped without changing either copy.")
                relative = source.relative_to(legacy)
                if relative.name in lock_names:
                    continue
                destination = state_dir / relative
                cursor = destination.parent
                while cursor != state_dir and state_dir in cursor.parents:
                    if cursor.is_symlink():
                        raise RuntimeError("Shared state tree contains a symlink; migration stopped safely.")
                    cursor = cursor.parent
                if destination.is_symlink():
                    raise RuntimeError("A migration target is a symlink; migration stopped safely.")
                if destination.exists():
                    if not destination.is_file() or source.read_bytes() != destination.read_bytes():
                        conflicts.append(relative.as_posix())
                else:
                    sources.append((source, destination))
        if conflicts:
            raise RuntimeError(
                "Legacy and shared POSIX state contain conflicting files; both copies were preserved: "
                + ", ".join(conflicts[:8])
            )
        # Re-create only missing parent directories. Existing state entries are
        # never chmod/chown'ed; preserve Runner/app access grants and file modes.
        for source, destination in sources:
            relative_parent = source.parent.relative_to(legacy)
            source_parent = legacy / relative_parent
            directory_chain = []
            current_source = source_parent
            while current_source != legacy:
                directory_chain.append(current_source)
                current_source = current_source.parent
            for source_directory in reversed(directory_chain):
                relative_directory = source_directory.relative_to(legacy)
                target_directory = state_dir / relative_directory
                if not target_directory.exists():
                    target_directory.mkdir(exist_ok=True)
                    metadata = source_directory.stat(follow_symlinks=False)
                    os.chown(target_directory, service_uid, metadata.st_gid)
                    os.chmod(target_directory, stat.S_IMODE(metadata.st_mode))
            descriptor, temporary_name = tempfile.mkstemp(prefix=".state-migration-", dir=destination.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                source_metadata = source.stat(follow_symlinks=False)
                shutil.copy2(source, temporary)
                os.chown(temporary, service_uid, source_metadata.st_gid)
                os.chmod(temporary, stat.S_IMODE(source_metadata.st_mode))
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        marker_payload = json.dumps(
            {"schema_version": 1, "target": str(state_dir), "migrated_at": __import__("time").time()},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8") + b"\n"
        _write_atomic(marker, marker_payload, 0o600)
    finally:
        import fcntl
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _systemd_unit(
    service_user: str,
    python: Path,
    service_script: Path,
    state_dir: Path,
    socket_path: Path,
) -> str:
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    args = " ".join(quote(value) for value in (
        str(python), "-I", "-S", str(service_script),
        "--state-dir", str(state_dir), "--socket-path", str(socket_path),
    ))
    user_line = "" if service_user == "root" else f"User={service_user}\n"
    return (
        "[Unit]\n"
        "Description=Personal AI Assistant local shared-state broker\n"
        "After=local-fs.target\n"
        "Before=multi-user.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"{user_line}"
        f"ExecStart={args}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "NoNewPrivileges=true\n"
        "ProtectSystem=strict\n"
        "ProtectHome=true\n"
        "PrivateTmp=false\n"
        "ProtectKernelTunables=true\n"
        "ProtectControlGroups=true\n"
        "RestrictAddressFamilies=AF_UNIX\n"
        "LockPersonality=true\n"
        "MemoryDenyWriteExecute=true\n"
        "CapabilityBoundingSet=\n"
        "AmbientCapabilities=\n"
        "UMask=000\n"
        f"ReadWritePaths={quote(str(state_dir))} {quote(str(socket_path.parent))}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _install_systemd(
    service_user: str,
    python: Path,
    service_script: Path,
    state_dir: Path,
    socket_path: Path,
) -> None:
    systemctl = shutil.which("systemctl")
    if not systemctl or not Path("/run/systemd/system").is_dir():
        raise RuntimeError("This Linux host is not running systemd; automatic POSIX broker service setup is not supported here.")
    unit_path = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
    unit_data = _systemd_unit(service_user, python, service_script, state_dir, socket_path).encode("utf-8")
    _write_atomic(unit_path, unit_data, 0o644)
    for command in (
        [systemctl, "daemon-reload"],
        [systemctl, "enable", "--now", f"{SERVICE_NAME}.service"],
        [systemctl, "restart", f"{SERVICE_NAME}.service"],
    ):
        result = _run(command, timeout=60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-1200:]
            raise RuntimeError(f"systemd service setup failed ({command[1]}): {detail}")


def _install_launchd(
    service_user: str,
    python: Path,
    service_script: Path,
    state_dir: Path,
    socket_path: Path,
) -> None:
    launchctl = shutil.which("launchctl")
    if not launchctl:
        raise RuntimeError("launchctl is unavailable; macOS broker service setup cannot continue.")
    plist_path = Path("/Library/LaunchDaemons") / f"{LAUNCHD_LABEL}.plist"
    arguments = [
        str(python), "-I", "-S", str(service_script),
        "--state-dir", str(state_dir), "--socket-path", str(socket_path),
    ]
    plist: dict[str, object] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "Umask": 0,
    }
    if service_user != "root":
        plist["UserName"] = service_user
    payload = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
    _write_atomic(plist_path, payload, 0o644)
    # bootout is idempotent: an unloaded service is expected on first install.
    _run([launchctl, "bootout", "system", str(plist_path)], timeout=20)
    result = _run([launchctl, "bootstrap", "system", str(plist_path)], timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise RuntimeError(f"launchd broker bootstrap failed: {detail}")
    result = _run([launchctl, "kickstart", "-k", f"system/{LAUNCHD_LABEL}"], timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise RuntimeError(f"launchd broker start failed: {detail}")


def _elevated_setup(
    state_dir: Path,
    config_dir: Path,
    service_python: Path,
    installer_uid: int,
    legacy_home: Path | None,
) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("POSIX broker installation requires administrator privileges.")
    _assert_no_active_runner_worker(sys.platform)
    if not state_dir.is_absolute() or not config_dir.is_absolute():
        raise RuntimeError("State and configuration paths must be absolute.")
    if not state_dir.is_absolute():
        raise RuntimeError("Shared state root must be absolute.")
    home_roots = tuple(Path(value) for value in ("/home", "/Users"))
    if any(state_dir == root or root in state_dir.parents for root in home_roots):
        raise RuntimeError("Shared state cannot be inside any per-user home directory.")
    if legacy_home is not None and (
        not legacy_home.is_absolute() or state_dir == legacy_home or legacy_home in state_dir.parents
    ):
        raise RuntimeError("Shared state cannot be inside the installing user's home directory.")
    # Reject symlinked ancestors before creating/changing any state permissions.
    cursor = Path(state_dir.anchor)
    for part in state_dir.parts[1:]:
        cursor = cursor / part
        try:
            current = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(current.st_mode):
            raise RuntimeError(f"Shared state path contains a symlink: {cursor}")
        if cursor != state_dir and not stat.S_ISDIR(current.st_mode):
            raise RuntimeError(f"Shared state path ancestor is not a directory: {cursor}")

    runner_uid = _discover_runner_service_uid(sys.platform)
    if state_dir.exists():
        info = state_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError("Shared POSIX state root must be a real directory.")
        service_uid = runner_uid if runner_uid is not None else info.st_uid
        if service_uid == 0:
            raise RuntimeError(
                "The shared state/Runner owner is root. Refusing to run the state broker as root; "
                "install the Runner under a dedicated non-root service identity or use a non-root-owned shared state root."
            )
    else:
        service_uid = runner_uid if runner_uid is not None else installer_uid
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chown(state_dir, service_uid, pwd.getpwuid(service_uid).pw_gid)
        os.chmod(state_dir, 0o700)
    _migrate_legacy_state(state_dir, legacy_home, service_uid)
    try:
        service_user = pwd.getpwuid(service_uid).pw_name
    except KeyError as exc:
        if service_uid == 0:
            service_user = "root"
        else:
            raise RuntimeError(f"Shared state directory owner UID {service_uid} has no local account.") from exc

    python = _select_service_python(service_uid, str(service_python))
    config_dir.mkdir(parents=True, exist_ok=True)
    if config_dir.is_symlink():
        raise RuntimeError("Machine broker configuration directory cannot be a symlink.")
    os.chown(config_dir, 0, -1)
    os.chmod(config_dir, 0o755)
    source = Path(__file__).resolve().parent / "autonomous_state_broker_service_posix.py"
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("POSIX state-broker service source is missing or unsafe.")
    service_script = config_dir / "state_broker_service.py"
    # Install the single shared Python state implementation with the isolated
    # service, rather than relying on PYTHONPATH or the installer checkout.
    package_dir = config_dir / "app"
    package_dir.mkdir(mode=0o755, exist_ok=True)
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise RuntimeError("Protected broker package directory is unsafe.")
    os.chown(package_dir, 0, -1)
    os.chmod(package_dir, 0o755)
    _write_atomic(package_dir / "__init__.py", b"", 0o644)
    for module in (
        "autonomous_engineering_state.py",
        "autonomous_engineering_state_broker_config.py",
        "autonomous_engineering_state_store.py",
        "autonomous_engineering_state_broker_contract.py",
        "autonomous_engineering_task_lifecycle.py",
        "autonomous_engineering_task_owner.py",
    ):
        module_source = source.parent.parent / "app" / module
        if module_source.is_symlink() or not module_source.is_file():
            raise RuntimeError("Broker state implementation is missing or unsafe.")
        _write_atomic(package_dir / module, module_source.read_bytes(), 0o644)
    _write_atomic(service_script, source.read_bytes(), 0o644)
    socket_hash = hashlib.sha256(str(state_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    socket_dir = Path("/tmp") / f"paa-state-broker-{socket_hash}"
    socket_path = socket_dir / "broker.sock"

    system = sys.platform
    if system.startswith("linux"):
        _install_systemd(service_user, python, service_script, state_dir, socket_path)
    elif system == "darwin":
        _install_launchd(service_user, python, service_script, state_dir, socket_path)
    else:
        raise RuntimeError(f"Automatic broker service setup is not implemented for {system}.")

    config = {
        "schema_version": 1,
        "state_directory": str(state_dir),
        "socket_path": str(socket_path),
        "service_uid": service_uid,
    }
    config_payload = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _write_atomic(config_dir / "autonomous-state-broker.json", config_payload, 0o644)

    # Verify a successful broker health response, not only that a socket path exists.
    request = json.dumps(
        {"version": 1, "method": "GET", "path": "/v1/health"},
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    deadline = __import__("time").monotonic() + 20
    last_error = None
    while __import__("time").monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(socket_path))
                client.sendall(request)
                response = bytearray()
                while len(response) <= 16 * 1024:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    newline = chunk.find(b"\n")
                    response.extend(chunk[:newline] if newline >= 0 else chunk)
                    if newline >= 0:
                        break
                envelope = json.loads(response.decode("utf-8"))
                payload = envelope.get("payload") if isinstance(envelope, dict) else None
                if (
                    isinstance(envelope, dict)
                    and envelope.get("version") == 1
                    and envelope.get("status") == 200
                    and isinstance(payload, dict)
                    and payload.get("ready") is True
                    and payload.get("schema_version") == 1
                ):
                    break
                last_error = RuntimeError("Broker health response was not ready.")
        except (OSError, ValueError, UnicodeError) as exc:
            last_error = exc
            __import__("time").sleep(0.25)
    else:
        raise RuntimeError(
            "POSIX state broker service did not pass its health check. "
            f"Last error: {type(last_error).__name__ if last_error else 'timeout'}."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elevated", action="store_true")
    parser.add_argument("--state-dir")
    parser.add_argument("--config-dir")
    parser.add_argument("--service-python")
    parser.add_argument("--installer-uid", type=int)
    parser.add_argument("--legacy-home")
    args = parser.parse_args()

    if sys.platform == "win32":
        raise SystemExit("Use the Windows service-broker installer on Windows.")
    if sys.platform not in {"linux", "darwin"}:
        raise SystemExit(f"Automatic POSIX broker setup is not supported on {sys.platform}.")

    if not args.elevated:
        project_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project_root))
        from app.autonomous_engineering_state import execution_state_directory
        from app.autonomous_engineering_state_broker_config import config_directory

        try:
            state_dir = execution_state_directory()
        except (OSError, RuntimeError, ValueError):
            configured = os.getenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "").strip()
            if configured:
                state_dir = Path(os.path.expandvars(configured)).expanduser()
                if not state_dir.is_absolute():
                    raise RuntimeError("AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute system path.")
            else:
                from app.autonomous_engineering_state import _shared_site_state_directory, _current_home_directory
                state_dir = _shared_site_state_directory(_current_home_directory())
        home = Path.home()
        service_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        command = [
            shutil.which("sudo") or "sudo",
            "--",
            sys.executable,
            "-I",
            "-S",
            str(Path(__file__).resolve()),
            "--elevated",
            "--state-dir", str(state_dir),
            "--config-dir", str(config_directory()),
            "--service-python", str(service_python),
            "--installer-uid", str(os.geteuid()),
            "--legacy-home", str(home),
        ]
        try:
            result = subprocess.run(command, check=False, shell=False, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SystemExit(f"Could not start elevated broker setup: {type(exc).__name__}") from exc
        return result.returncode

    if not all((args.state_dir, args.config_dir, args.service_python)) or args.installer_uid is None:
        raise SystemExit("Elevated broker setup is missing required machine paths or identity.")
    _elevated_setup(
        Path(args.state_dir),
        Path(args.config_dir),
        Path(args.service_python),
        args.installer_uid,
        Path(args.legacy_home) if args.legacy_home else None,
    )
    print("POSIX machine state broker is installed and healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
