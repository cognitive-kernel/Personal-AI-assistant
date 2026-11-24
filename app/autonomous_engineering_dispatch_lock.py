"""Cross-process, restart-persistent admission lock for UI workflow dispatches."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .autonomous_engineering_state import execution_state_directory


def _open_lock(path: Path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    return os.fdopen(descriptor, "r+b")


@contextmanager
def dispatch_admission_lock() -> Iterator[dict[str, float]]:
    """Lock dispatch admission across workers and persist the last accepted time.

    Byte zero is reserved for the OS lock. JSON state follows it, so Windows
    byte-range locking never overlaps the state bytes being read or written.
    """
    directory = execution_state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    handle = _open_lock(directory / "workflow-dispatch-admission.lock")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another process is currently deciding workflow dispatch admission.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another process is currently deciding workflow dispatch admission.") from exc
        acquired = True
        handle.seek(1)
        raw = handle.read(4096)
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Persistent workflow dispatch admission state is corrupt; refusing dispatch.") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Persistent workflow dispatch admission state is invalid; refusing dispatch.")
            last = payload.get("last_accepted_at", 0.0)
            if isinstance(last, bool) or not isinstance(last, (int, float)) or last < 0:
                raise RuntimeError("Persistent workflow dispatch timestamp is invalid; refusing dispatch.")
        else:
            last = 0.0
        state = {"last_accepted_at": float(last)}
        yield state
        updated = json.dumps(
            {"schema_version": 1, "last_accepted_at": state["last_accepted_at"]},
            separators=(",", ":"),
        ).encode("utf-8")
        handle.seek(1)
        handle.write(updated)
        handle.truncate(1 + len(updated))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
