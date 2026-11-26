#!/usr/bin/env python3
"""Restricted POSIX Unix-domain-socket broker for shared autonomous state.

This file intentionally uses only the Python standard library. It is installed
as a protected machine file and runs under the owner identity of the shared
state directory (discovered by the installer).
"""
from __future__ import annotations

import argparse
import struct
import ctypes
import json
import os
import signal
import socket
import stat
import threading
import sys
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_WORKERS = 8
# Under -I -S the installed service has no implicit script-directory import.
# Both the protected installed package and the source checkout use this core.
_here = Path(__file__).resolve().parent
_package_root = _here if (_here / "app" / "autonomous_engineering_state_store.py").is_file() else _here.parent
sys.path.insert(0, str(_package_root))
from app.autonomous_engineering_state_store import BrokerFailure, RestrictedStateBroker

class UnixSocketBroker:
    def __init__(self, broker: RestrictedStateBroker, socket_path: Path, *, client_uid: int | None = None):
        self.broker = broker
        self.socket_path = socket_path
        self.allowed_uids = {os.geteuid(), 0, os.geteuid() if client_uid is None else client_uid}
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)

    def _prepare_socket_path(self) -> None:
        parent = self.socket_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError("Broker socket parent must be a real directory.")
        if info.st_uid != os.geteuid():
            raise RuntimeError("Broker socket directory must be owned by the broker service identity.")
        if info.st_mode & 0o022:
            raise RuntimeError("Broker socket directory cannot be group/world writable.")
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
            raise RuntimeError("Refusing to replace a non-socket or foreign-owned broker path.")
        self.socket_path.unlink()

    @staticmethod
    def _read_line(client: socket.socket) -> bytes:
        buffer = bytearray()
        while len(buffer) <= MAX_REQUEST_BYTES:
            chunk = client.recv(4096)
            if not chunk:
                break
            newline = chunk.find(b"\n")
            buffer.extend(chunk[:newline] if newline >= 0 else chunk)
            if len(buffer) > MAX_REQUEST_BYTES:
                raise BrokerFailure(413, "request_too_large")
            if newline >= 0:
                return bytes(buffer)
        if not buffer:
            raise BrokerFailure(400, "empty_request")
        return bytes(buffer)

    @staticmethod
    def _write(client: socket.socket, status: int, payload: Any) -> None:
        data = json.dumps(
            {"version": 1, "status": status, "payload": payload},
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        if len(data) > MAX_RESPONSE_BYTES:
            data = json.dumps(
                {"version": 1, "status": 500, "payload": {"error": "response_too_large"}},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        client.sendall(data)

    @staticmethod
    def _peer_uid(client: socket.socket) -> int:
        """Use kernel-authenticated peer identity, never request metadata."""
        if hasattr(socket, "SO_PEERCRED"):
            size = struct.calcsize("3i")
            _, uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size))
            return uid
        if sys.platform == "darwin":
            libc = ctypes.CDLL(None, use_errno=True)
            uid, gid = ctypes.c_uint(), ctypes.c_uint()
            fn = libc.getpeereid
            fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
            fn.restype = ctypes.c_int
            if fn(client.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
                raise OSError(ctypes.get_errno(), "Cannot authenticate broker peer.")
            return uid.value
        raise OSError("Peer authentication unavailable.")

    def _serve_one(self, client: socket.socket) -> None:
        try:
            client.settimeout(3.0)
            if self._peer_uid(client) not in self.allowed_uids:
                self._write(client, 403, {"error": "peer_not_authorized"})
                return
            raw = self._read_line(client)
            try:
                request = json.loads(raw.decode("utf-8"))
                status, payload = self.broker.dispatch(request)
            except BrokerFailure as exc:
                status, payload = exc.status, {"error": exc.code}
            except (UnicodeDecodeError, ValueError):
                status, payload = 400, {"error": "malformed_request"}
            except (OSError, TimeoutError):
                status, payload = 503, {"error": "storage_unavailable"}
            self._write(client, status, payload)
        except (OSError, TimeoutError):
            pass
        finally:
            client.close()
            self.slots.release()

    def serve_forever(self) -> None:
        self._prepare_socket_path()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        listener.listen(MAX_WORKERS)
        listener.settimeout(1.0)
        try:
            while not self.stopping.is_set():
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                if not self.slots.acquire(blocking=False):
                    try:
                        self._write(client, 503, {"error": "broker_busy"})
                    finally:
                        client.close()
                    continue
                threading.Thread(target=self._serve_one, args=(client,), daemon=True).start()
        finally:
            listener.close()
            try:
                if self.socket_path.is_socket() and self.socket_path.lstat().st_uid == os.geteuid():
                    self.socket_path.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--socket-path", required=True)
    parser.add_argument("--client-uid", type=int)
    args = parser.parse_args()
    broker = RestrictedStateBroker(Path(args.state_dir))
    server = UnixSocketBroker(broker, Path(args.socket_path), client_uid=args.client_uid)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: server.stopping.set())
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
