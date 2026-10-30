"""Allow-listed task dispatcher; deliberately does not execute arbitrary payloads."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
import threading
import time
from typing import Any, Callable

from . import task_manager, usage_metrics
from .task_manager import TaskManagerError

TaskHandler = Callable[[dict[str, Any], dict[str, Any] | None, "TaskContext"], dict[str, Any]]
logger = logging.getLogger("personal_ai_assistant.task_worker")


class RetryableTaskError(RuntimeError):
    """Explicit handler contract for a domain failure that may succeed on retry."""


class PermanentTaskError(ValueError):
    """Explicit handler contract for a domain failure that must not be retried.

    It remains a ValueError subclass for compatibility with callers that already
    treat invalid domain inputs as validation failures.
    """


def is_retryable_task_error(error: Exception) -> bool:
    """Honor explicit handler policy, then preserve the legacy exception fallback."""
    if isinstance(error, PermanentTaskError):
        return False
    if isinstance(error, RetryableTaskError):
        return True
    return not isinstance(
        error,
        (ValueError, TypeError, KeyError, AssertionError, NotImplementedError, sqlite3.IntegrityError),
    )


class TaskInterrupted(RuntimeError):
    """Raised when a cooperative pause or cancellation has been settled."""


@dataclass
class TaskContext:
    task_id: int
    worker_id: str
    checkpoint_data: dict[str, Any] | None = None
    lease_seconds: int = 300

    def checkpoint(self, value: dict[str, Any]) -> dict[str, Any]:
        saved = task_manager.save_checkpoint(self.task_id, self.worker_id, value)
        self.checkpoint_data = saved["checkpoint"]
        if saved["status"] == "paused":
            raise TaskInterrupted("Task paused at checkpoint.")
        if saved["status"] == "cancelled":
            raise TaskInterrupted("Task cancelled at checkpoint.")
        return saved

    def control_status(self) -> str:
        return task_manager.get_worker_control(self.task_id, self.worker_id)

    def heartbeat(self) -> dict[str, Any]:
        status = self.control_status()
        if status != "running":
            self.checkpoint(self.checkpoint_data or {})
        return task_manager.renew_lease(
            self.task_id, self.worker_id, lease_seconds=self.lease_seconds
        )


class TaskWorker:
    """Execute one task from a strict registry of application-owned handlers."""

    def __init__(
        self,
        worker_id: str,
        handlers: dict[str, TaskHandler],
        *,
        lease_seconds: int = 300,
        restrict_task_types: bool = False,
        exclude_task_types: tuple[str, ...] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer.")
        if not isinstance(handlers, dict) or not handlers or any(
            not isinstance(key, str) or not key.strip() or not callable(value)
            for key, value in handlers.items()
        ):
            raise ValueError("handlers must map non-empty task types to callable handlers.")
        self.worker_id = worker_id.strip()
        if exclude_task_types is not None and (
            not isinstance(exclude_task_types, tuple) or not exclude_task_types
            or any(not isinstance(item, str) or not item.strip() for item in exclude_task_types)
        ):
            raise ValueError("exclude_task_types must be a non-empty tuple of non-empty strings.")
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self.restrict_task_types = bool(restrict_task_types)
        self.exclude_task_types = exclude_task_types

    def _create_heartbeat_thread(
        self, context: TaskContext, stop_event: threading.Event
    ) -> threading.Thread:
        """Create a separately patchable heartbeat thread for deterministic failure tests."""
        return threading.Thread(
            target=self._maintain_lease,
            args=(context, stop_event),
            name=f"{self.worker_id}-lease-{context.task_id}",
            daemon=True,
        )

    def _maintain_lease(self, context: TaskContext, stop_event: threading.Event) -> None:
        """Renew a running task lease while its handler is doing long-running work."""
        interval = max(0.05, min(30.0, self.lease_seconds / 3))
        # A transient SQLite lock must not silently disable lease renewal. Retry
        # operational storage failures only while the last known lease can remain
        # valid; ownership loss and unexpected programming errors still stop it.
        lease_deadline = time.monotonic() + self.lease_seconds
        while True:
            remaining = lease_deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "Task lease heartbeat stopped after its renewal deadline for task %s.",
                    context.task_id,
                )
                return
            if stop_event.wait(min(interval, remaining)):
                return
            try:
                # Do not extend a lease after pause/cancel has been requested. If the
                # handler cannot reach a checkpoint, expiry lets the queue settle it.
                if context.control_status() != "running":
                    return
                task_manager.renew_lease(
                    context.task_id, context.worker_id, lease_seconds=self.lease_seconds
                )
                lease_deadline = time.monotonic() + self.lease_seconds
            except TaskManagerError:
                # Ownership loss or a control-state transition ends heartbeat ownership.
                return
            except sqlite3.OperationalError:
                # SQLite busy/locked errors can be transient; retry without allowing
                # the worker to claim that its lease is still renewable indefinitely.
                logger.exception(
                    "Task lease heartbeat storage operation failed; retrying before expiry for task %s.",
                    context.task_id,
                )
            except Exception:
                logger.exception("Task lease heartbeat failed for task %s", context.task_id)
                return

    def run_once(self) -> bool:
        """Claim and process one task. Return False only when no task is ready."""
        claim_options: dict[str, Any] = {"lease_seconds": self.lease_seconds}
        if self.restrict_task_types:
            claim_options["task_types"] = tuple(self.handlers)
        if self.exclude_task_types is not None:
            claim_options["exclude_task_types"] = self.exclude_task_types
        task = task_manager.claim_next_task(self.worker_id, **claim_options)
        if task is None:
            return False

        handler = self.handlers.get(task["task_type"])
        if handler is None:
            task_manager.fail_task(
                task["id"],
                self.worker_id,
                f"No registered handler for task type: {task['task_type']}",
                retry=False,
            )
            return True

        context = TaskContext(
            task_id=task["id"],
            worker_id=self.worker_id,
            checkpoint_data=task["checkpoint"],
            lease_seconds=self.lease_seconds,
        )
        heartbeat_stop = threading.Event()
        try:
            heartbeat_thread = self._create_heartbeat_thread(context, heartbeat_stop)
            heartbeat_thread.start()
        except Exception as exc:
            # A task has already been claimed at this point. If the OS refuses
            # to start the lease heartbeat, do not execute a potentially long
            # handler without ownership renewal or strand the task until expiry.
            heartbeat_stop.set()
            try:
                task_manager.fail_task(
                    task["id"],
                    self.worker_id,
                    f"HeartbeatStartupError: {type(exc).__name__}: {exc}",
                    retry=True,
                )
            except Exception:
                # If storage is unavailable too, the durable lease expiry remains
                # the final recovery mechanism; keep the worker loop alive.
                logger.exception(
                    "Could not settle task %s after heartbeat thread startup failed.",
                    task["id"],
                )
            return True
        try:
            with usage_metrics.capture_task(task["id"]):
                result = handler(task["payload"], task["checkpoint"], context)
            if not isinstance(result, dict):
                raise TypeError("Task handlers must return a JSON object.")
            control = context.control_status()
            if control != "running":
                context.checkpoint(context.checkpoint_data or {})
                return True
            task_manager.complete_task(task["id"], self.worker_id, result)
        except TaskInterrupted:
            # Checkpoint settled a pause/cancel request; do not overwrite that state.
            return True
        except TaskManagerError:
            # A pause/cancel request can race with completion. Settle it if this
            # worker still owns a valid lease; otherwise leave state to lease recovery.
            try:
                control = context.control_status()
                if control != "running":
                    context.checkpoint(context.checkpoint_data or {})
            except (TaskInterrupted, TaskManagerError):
                pass
            return True
        except Exception as exc:
            try:
                control = context.control_status()
                if control != "running":
                    context.checkpoint(context.checkpoint_data or {})
                else:
                    task_manager.fail_task(
                        task["id"],
                        self.worker_id,
                        f"{type(exc).__name__}: {exc}",
                        retry=is_retryable_task_error(exc),
                    )
            except (TaskInterrupted, TaskManagerError):
                # Control requests and lease loss take precedence over failure writes.
                pass
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(0.1, min(1.0, self.lease_seconds / 3)))
        return True
