#!/usr/bin/env python3
"""Restricted POSIX Unix-domain-socket broker for shared autonomous state.

This file intentionally uses only the Python standard library. It is installed
as a protected machine file and runs under the owner identity of the shared
state directory (discovered by the installer).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import signal
import socket
import stat
import threading
import time
import uuid
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
        supplied = Path(state_dir)
        try:
            info = supplied.lstat()
        except OSError as exc:
            raise RuntimeError("Shared state root is unavailable.") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Shared state root must be a real directory, not a symlink.")
        self.state_dir = supplied.resolve(strict=True)

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

    @staticmethod
    def _unknown_checkpoint(reason: str) -> dict[str, Any]:
        return {"available": True, "verified": False, "reason": reason}

    @staticmethod
    def _no_checkpoint() -> dict[str, Any]:
        return {"available": False, "verified": True}

    @staticmethod
    def _safe_checkpoint_run_id(value: Any) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= 100
            and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in value)
        )

    def _checkpoint_summary(self) -> dict[str, Any]:
        """Read the atomic pointer or the current run's legacy checkpoint; return no paths."""
        index_path = self.state_dir / "latest-worktree-checkpoint.json"
        index_info = self._regular_file(index_path, 16 * 1024)
        index: dict[str, Any] | None = None
        if index_info is None:
            current_path = self.state_dir / "current.json"
            if self._regular_file(current_path, 256_000) is None:
                return self._no_checkpoint()
            current = self._read_json("current.json", 256_000)
            if not isinstance(current, dict):
                return self._unknown_checkpoint("current_progress_unreadable")
            if "run_id" not in current:
                return self._no_checkpoint()
            run_id = current.get("run_id")
            task = current.get("task", 1)
        else:
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                return self._unknown_checkpoint("checkpoint_index_unreadable")
            if (
                not isinstance(index, dict)
                or index.get("schema_version") != 1
                or not isinstance(index.get("active"), bool)
            ):
                return self._unknown_checkpoint("checkpoint_index_invalid")
            if index["active"] is False:
                return self._no_checkpoint()
            run_id = index.get("run_id")
            task = index.get("task")

        if (
            not self._safe_checkpoint_run_id(run_id)
            or isinstance(task, bool) or not isinstance(task, int) or task < 1
        ):
            return self._unknown_checkpoint("checkpoint_index_identity_invalid")

        stem = f"worktree-checkpoint-{run_id}-{task}"
        metadata_path = self.state_dir / f"{stem}.json"
        patch_path = self.state_dir / f"{stem}.patch"
        metadata_info = self._regular_file(metadata_path, 64 * 1024)
        patch_info = self._regular_file(patch_path)
        if index is None and metadata_info is None and patch_info is None:
            return self._no_checkpoint()
        if metadata_info is None or patch_info is None:
            return self._unknown_checkpoint("checkpoint_files_incomplete")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return self._unknown_checkpoint("checkpoint_metadata_unreadable")
        if not isinstance(metadata, dict):
            return self._unknown_checkpoint("checkpoint_metadata_invalid")

        schema = metadata.get("schema_version")
        saved_run_id = metadata.get("run_id")
        saved_task = metadata.get("task")
        base_sha = metadata.get("base_sha")
        updated_at = metadata.get("updated_at")
        patch_bytes = metadata.get("patch_bytes")
        if (
            isinstance(schema, bool) or schema not in {2, 3, 4}
            or saved_run_id != run_id
            or isinstance(saved_task, bool) or saved_task != task
            or not isinstance(base_sha, str) or len(base_sha) != 40
            or any(ch not in "0123456789abcdef" for ch in base_sha)
            or isinstance(updated_at, bool) or not isinstance(updated_at, (int, float))
            or not math.isfinite(float(updated_at))
            or isinstance(patch_bytes, bool) or not isinstance(patch_bytes, int) or patch_bytes < 0
            or patch_bytes != patch_info.st_size
        ):
            return self._unknown_checkpoint("checkpoint_metadata_mismatch")

        if index is not None:
            index_bytes = index.get("patch_bytes")
            index_updated = index.get("updated_at")
            if (
                index.get("base_sha") != base_sha
                or isinstance(index_bytes, bool) or not isinstance(index_bytes, int)
                or index_bytes != patch_info.st_size
                or isinstance(index_updated, bool) or not isinstance(index_updated, (int, float))
                or not math.isfinite(float(index_updated))
                or float(index_updated) != float(updated_at)
            ):
                return self._unknown_checkpoint("checkpoint_index_mismatch")

        return {
            "available": True,
            "verified": True,
            "run_id": run_id,
            "task": task,
            "base_sha": base_sha,
            "updated_at": float(updated_at),
            "patch_bytes": patch_info.st_size,
            "plan_available": self._regular_file(self.state_dir / f"worktree-plan-{run_id}-{task}.json") is not None,
        }

    def pending_work(self) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            present: dict[str, bool] = {}
            for key, filename in MARKERS.items():
                present[key] = self._regular_file(self.state_dir / filename) is not None
            checkpoint = self._checkpoint_summary()
            blockers = [key for key, exists in present.items() if exists]
            if checkpoint.get("available") is True or checkpoint.get("verified") is False:
                blockers.append("checkpoint")
            return {
                "available": True,
                "blocked": bool(blockers),
                "markers": present,
                "checkpoint": checkpoint,
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
        diagnostics_dir = self.state_dir / "run-diagnostics"
        try:
            directory_info = diagnostics_dir.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(directory_info.st_mode) or stat.S_ISLNK(directory_info.st_mode):
            raise OSError("Run diagnostics directory is not a real directory.")
        path = diagnostics_dir / f"{run_id}.json"
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

    def _dispatch_admission_state(self) -> dict[str, Any]:
        path = self.state_dir / "workflow-dispatch-admission.json"
        info = self._regular_file(path)
        if info is None:
            return {"schema_version": 1, "last_accepted_at": 0.0, "pending": None}
        if info.st_size > 4096:
            raise BrokerFailure(503, "dispatch_admission_state_invalid")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise BrokerFailure(503, "dispatch_admission_state_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise BrokerFailure(503, "dispatch_admission_state_invalid")
        last = payload.get("last_accepted_at")
        if isinstance(last, bool) or not isinstance(last, (int, float)) or not math.isfinite(last) or last < 0:
            raise BrokerFailure(503, "dispatch_admission_state_invalid")
        pending = payload.get("pending")
        if pending is not None:
            if not isinstance(pending, dict):
                raise BrokerFailure(503, "dispatch_admission_state_invalid")
            request_id = pending.get("request_id")
            created_at = pending.get("created_at")
            expires_at = pending.get("lease_expires_at")
            if (
                not isinstance(request_id, str)
                or len(request_id) != 32
                or any(ch not in "0123456789abcdef" for ch in request_id)
                or isinstance(created_at, bool)
                or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at)
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, (int, float))
                or not math.isfinite(expires_at)
                or created_at < 0
                or expires_at <= created_at
            ):
                raise BrokerFailure(503, "dispatch_admission_state_invalid")
        return {"schema_version": 1, "last_accepted_at": float(last), "pending": pending}

    def _write_dispatch_admission_state(self, payload: dict[str, Any]) -> None:
        target = self.state_dir / "workflow-dispatch-admission.json"
        temporary = self.state_dir / (".workflow-dispatch-admission-" + uuid.uuid4().hex + ".tmp")
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\\n"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("Could not write dispatch admission state.")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, target)
            directory_fd = os.open(self.state_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def dispatch_admission(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        from app.autonomous_engineering_state_broker_contract import validate_dispatch_admission_request

        try:
            request = validate_dispatch_admission_request("POST", path, body)
        except ValueError as exc:
            raise BrokerFailure(400, "invalid_dispatch_admission_request") from exc

        def perform() -> dict[str, Any]:
            state = self._dispatch_admission_state()
            now = time.time()
            pending = state["pending"]
            last_accepted = state["last_accepted_at"]
            if path.endswith("/acquire"):
                if pending is not None:
                    if now < pending["lease_expires_at"]:
                        raise BrokerFailure(409, "dispatch_admission_conflict")
                    # A crashed caller may have dispatched before losing its
                    # response. Treat an expired lease as ambiguous, not free.
                    # Start a fresh grace interval from lease recovery, not
                    # from the old reservation creation time.
                    last_accepted = max(last_accepted, now)
                    pending = None
                    self._write_dispatch_admission_state({
                        "schema_version": 1,
                        "last_accepted_at": last_accepted,
                        "pending": None,
                    })
                if last_accepted and (now < last_accepted or now - last_accepted < 30.0):
                    raise BrokerFailure(409, "dispatch_admission_conflict")
                state = {
                    "schema_version": 1,
                    "last_accepted_at": last_accepted,
                    "pending": {
                        "request_id": request["request_id"],
                        "created_at": now,
                        "lease_expires_at": now + 120.0,
                    },
                }
                self._write_dispatch_admission_state(state)
                return {"acquired": True, "last_accepted_at": last_accepted, "lease_seconds": 120}
            if pending is None or pending.get("request_id") != request["request_id"]:
                raise BrokerFailure(409, "dispatch_admission_lease_not_owned")
            if request["accepted"]:
                last_accepted = now
            state = {"schema_version": 1, "last_accepted_at": last_accepted, "pending": None}
            self._write_dispatch_admission_state(state)
            return {"completed": True, "last_accepted_at": last_accepted}

        try:
            return self._with_state_lock(perform)
        except BrokerFailure:
            raise
        except OSError as exc:
            raise BrokerFailure(503, "dispatch_admission_state_unavailable") from exc

    def cleanup(self) -> dict[str, Any]:
        def perform() -> dict[str, Any]:
            # The watcher owns this lock for its full lifetime. Hold it across
            # both the checkpoint check and telemetry cleanup so a new watcher
            # cannot publish a checkpoint between the check and file removal.
            lock_path = self.state_dir / "autonomous-checkpoint-store.lock"
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_path, flags, 0o600)
            locked = False
            try:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    return {
                        "conflict": True,
                        "blockers": ["checkpoint_writer_active"],
                        "message": "A checkpoint watcher owns the durable checkpoint store; cleanup was refused.",
                    }
                blockers = [
                    filename
                    for filename in MARKERS.values()
                    if self._regular_file(self.state_dir / filename) is not None
                ]
                checkpoint = self._checkpoint_summary()
                if checkpoint.get("available") is True or checkpoint.get("verified") is False:
                    blockers.append("latest-worktree-checkpoint")
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
            finally:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        return self._with_state_lock(perform)

    def _with_task_mutation_locks(self, operation):
        from autonomous_pending_task_lifecycle import LifecycleConflict

        def with_lock(filename: str, blocker: str, callback):
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.state_dir / filename, flags, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise LifecycleConflict(
                        "A task lifecycle writer lock is active.", [blocker],
                    ) from exc
                try:
                    return callback()
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

        # Match the Windows service and Runner global order:
        # repository execution -> durable state -> checkpoint store.
        return with_lock(
            "repository-execution.lock",
            "repository_execution_active",
            lambda: self._with_state_lock(
                lambda: with_lock(
                    "autonomous-checkpoint-store.lock",
                    "checkpoint_writer_active",
                    operation,
                )
            ),
        )

    def dispatch(self, request: Any) -> tuple[int, Any]:
        if not isinstance(request, dict) or request.get("version") != 1:
            raise BrokerFailure(400, "invalid_request")
        if set(request) not in (
            {"version", "method", "path"},
            {"version", "method", "path", "body"},
        ):
            raise BrokerFailure(400, "request_fields_not_supported")
        method, path = request.get("method"), request.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise BrokerFailure(400, "invalid_request")
        method = method.upper()
        body = request.get("body")
        task_paths = {
            "/v1/tasks/pending", "/v1/tasks/archived",
            "/v1/tasks/archive", "/v1/tasks/restore",
        }
        if path in task_paths:
            from app.autonomous_engineering_state_broker_contract import validate_task_request
            try:
                validated = validate_task_request(method, path, body)
            except ValueError as exc:
                raise BrokerFailure(400, "invalid_task_request") from exc
            from autonomous_pending_task_lifecycle import (
                LifecycleConflict, archive_task, list_archived, list_pending, restore_task,
            )
            try:
                if method == "GET" and path == "/v1/tasks/pending":
                    return 200, self._with_state_lock(lambda: {
                        "available": True, "tasks": list_pending(self.state_dir),
                    })
                if method == "GET" and path == "/v1/tasks/archived":
                    return 200, self._with_state_lock(lambda: {
                        "available": True, "tasks": list_archived(self.state_dir),
                    })
                if method == "POST" and path == "/v1/tasks/archive":
                    assert validated is not None
                    return 200, self._with_task_mutation_locks(
                        lambda: archive_task(
                            self.state_dir, validated["task_id"], validated["revision"], locks_held=True,
                        )
                    )
                if method == "POST" and path == "/v1/tasks/restore":
                    assert validated is not None
                    return 200, self._with_task_mutation_locks(
                        lambda: restore_task(
                            self.state_dir, validated["task_id"], validated["revision"], locks_held=True,
                        )
                    )
            except LifecycleConflict as exc:
                return 409, {"conflict": True, "message": str(exc), "blockers": exc.blockers}
            except BrokerFailure as exc:
                if exc.code == "state_lock_timeout":
                    return 409, {
                        "conflict": True,
                        "message": "The durable task state lock is held by an active writer.",
                        "blockers": ["durable_state_writer_active"],
                    }
                raise
            raise BrokerFailure(404, "not_found")
        dispatch_paths = {"/v1/dispatch-admission/acquire", "/v1/dispatch-admission/complete"}
        if path in dispatch_paths:
            if method != "POST" or not isinstance(body, dict):
                raise BrokerFailure(400, "invalid_dispatch_admission_request")
            try:
                return 200, self.dispatch_admission(path, body)
            except BrokerFailure as exc:
                if exc.status == 409:
                    return 409, {"conflict": True, "message": exc.code, "blockers": [exc.code]}
                raise
        if "body" in request:
            raise BrokerFailure(400, "request_body_not_supported")
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
