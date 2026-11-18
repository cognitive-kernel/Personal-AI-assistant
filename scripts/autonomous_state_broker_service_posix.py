#!/usr/bin/env python3
"""Restricted POSIX Unix-domain-socket broker for shared autonomous state.

This file intentionally uses only the Python standard library. It is installed
as a protected machine file and runs under the owner identity of the shared
state directory (discovered by the installer).
"""
from __future__ import annotations

import argparse
import array
import fcntl
import json
import os
import signal
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_WORKERS = 8
MARKERS = {
    "pending_task": "pending-manual-task.txt",
    "pending_owner": "pending-manual-task-owner.json",
    "pending_pr": "pending-autonomous-pr.json",
    "cleanup_in_progress": "pending-state-cleanup.json",
}
CLEANUP_NAMES = (
    "events.jsonl",
    "current.json",
    "events.jsonl.tmp",
    "current.json.tmp",
    "history.jsonl.tmp",
    "scorecard-state.json.tmp",
    "scorecard-baseline.json.tmp",
)


class BrokerFailure(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class RestrictedStateBroker:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.resolve(strict=True)
        if not self.state_dir.is_dir() or self.state_dir.is_symlink():
            raise RuntimeError("Shared state root must be a real directory.")

    @staticmethod
    def _regular_file(path: Path, maximum_bytes: int | None = None) -> os.stat_result | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise OSError("State entry is not a regular file.")
        if maximum_bytes is not None and info.st_size > maximum_bytes:
            return None
        return info

    def _read_json(self, name: str, maximum_bytes: int) -> Any:
        path = self.state_dir / name
        info = self._regular_file(path, maximum_bytes)
        if info is None:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _read_json_lines(
        self, name: str, maximum_bytes: int, maximum_lines: int, required_type: str
    ) -> list[dict[str, Any]]:
        path = self.state_dir / name
        info = self._regular_file(path, maximum_bytes)
        if info is None:
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-maximum_lines:]
        except (OSError, UnicodeError):
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("type") == required_type:
                records.append(item)
        return records

    def _with_state_lock(self, operation):
        lock_path = self.state_dir / "autonomous-durable-state.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        deadline = time.monotonic() + 2.0
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise BrokerFailure(503, "state_lock_timeout") from exc
                    time.sleep(0.05)
            try:
                return operation()
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def health(self) -> dict[str, Any]:
        probe = self.state_dir / (".state-broker-probe-" + os.urandom(12).hex() + ".tmp")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(probe, flags, 0o600)
        try:
            os.write(descriptor, b"A")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            probe.unlink(missing_ok=True)
        return {"ok": True, "ready": True, "service": "posix-unix-socket", "schema_version": 1}

    def pending_work(self) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            present: dict[str, bool] = {}
            for key, filename in MARKERS.items():
                present[key] = self._regular_file(self.state_dir / filename) is not None
            blockers = [key for key, exists in present.items() if exists]
            return {
                "available": True,
                "blocked": bool(blockers),
                "markers": present,
                "reason": ", ".join(blockers) if blockers else None,
            }
        return self._with_state_lock(read)

    def progress(self) -> dict[str, Any]:
        events = self._read_json_lines("events.jsonl", 1024 * 1024, 1000, "progress_event")
        safe_events = []
        for item in events[-50:]:
            safe_events.append({
                key: item[key]
                for key in ("updated_at", "phase_label", "activity", "percent", "status")
                if key in item
            })
        return {
            "current": self._read_json("current.json", 256_000),
            "baseline": self._read_json("scorecard-baseline.json", 256_000),
            "history": self._read_json_lines("history.jsonl", 8 * 1024 * 1024, 20_000, "project_snapshot"),
            "events": safe_events,
            "storage_configured": self.state_dir.is_dir(),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def diagnostics(self, run_id: str) -> dict[str, Any] | None:
        if not run_id.isascii() or not run_id.isdigit() or not (1 <= len(run_id) <= 20) or int(run_id) <= 0:
            raise BrokerFailure(400, "invalid_run_id")
        path = self.state_dir / "run-diagnostics" / f"{run_id}.json"
        info = self._regular_file(path, 240_000)
        if info is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or str(payload.get("run_id")) != run_id
            or not isinstance(payload.get("diagnostics"), list)
        ):
            return None
        entries = []
        for item in payload["diagnostics"][-8:]:
            if not isinstance(item, dict):
                continue
            safe: dict[str, Any] = {}
            for field in ("run_attempt", "stage", "category", "summary", "detail", "recorded_at"):
                if field not in item:
                    continue
                limit = 12_000 if field == "detail" else 800 if field == "summary" else 100
                safe[field] = str(item[field])[:limit]
            for field in ("task", "attempt"):
                value = item.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    safe[field] = value
            entries.append(safe)
        return {
            "schema_version": 1,
            "run_id": run_id,
            "updated_at": payload.get("updated_at", ""),
            "diagnostics": entries,
        }

    def cleanup(self) -> dict[str, Any]:
        def perform() -> dict[str, Any]:
            blockers = [
                filename
                for filename in MARKERS.values()
                if self._regular_file(self.state_dir / filename) is not None
            ]
            if blockers:
                return {
                    "conflict": True,
                    "blockers": blockers,
                    "message": "Durable task state is pending; cleanup was refused.",
                }
            removed = 0
            removed_bytes = 0
            failed: list[str] = []
            for name in CLEANUP_NAMES:
                path = self.state_dir / name
                try:
                    info = self._regular_file(path)
                    if info is None:
                        continue
                    path.unlink()
                    removed += 1
                    removed_bytes += info.st_size
                except FileNotFoundError:
                    continue
                except OSError:
                    failed.append(name)
            return {
                "conflict": False,
                "cleaned": not failed,
                "files_removed": removed,
                "bytes_removed": removed_bytes,
                "failed_files": failed,
                "preserved": [
                    "history.jsonl",
                    "scorecard-baseline.json",
                    "checkpoint files",
                    "pending task/PR markers",
                ],
            }
        return self._with_state_lock(perform)

    def dispatch(self, request: Any) -> tuple[int, Any]:
        if not isinstance(request, dict) or request.get("version") != 1:
            raise BrokerFailure(400, "invalid_request")
        if set(request) != {"version", "method", "path"}:
            raise BrokerFailure(400, "request_fields_not_supported")
        method, path = request.get("method"), request.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise BrokerFailure(400, "invalid_request")
        method = method.upper()
        if method == "GET" and path == "/v1/health":
            try:
                return 200, self.health()
            except OSError:
                return 503, {"ok": True, "ready": False, "service": "posix-unix-socket", "schema_version": 1}
        if method == "GET" and path == "/v1/pending-work":
            return 200, self.pending_work()
        if method == "GET" and path == "/v1/progress":
            return 200, self.progress()
        prefix = "/v1/diagnostics/"
        if method == "GET" and path.startswith(prefix):
            value = self.diagnostics(path[len(prefix):])
            return (200, value) if value is not None else (404, {"error": "not_found"})
        if method == "POST" and path == "/v1/cleanup":
            value = self.cleanup()
            return (409 if value.get("conflict") else 200), value
        raise BrokerFailure(404, "not_found")


class UnixSocketBroker:
    def __init__(self, broker: RestrictedStateBroker, socket_path: Path):
        self.broker = broker
        self.socket_path = socket_path
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

    def _serve_one(self, client: socket.socket) -> None:
        try:
            client.settimeout(3.0)
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
        os.chmod(self.socket_path, 0o666)
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
    args = parser.parse_args()
    broker = RestrictedStateBroker(Path(args.state_dir))
    server = UnixSocketBroker(broker, Path(args.socket_path))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: server.stopping.set())
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
