"""Allow-listed task dispatcher; deliberately does not execute arbitrary payloads."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
import threading
from typing import Any, Callable

from . import task_manager
from .task_manager import TaskManagerError

TaskHandler = Callable[[dict[str, Any], dict[str, Any] | None, "TaskContext"], dict[str, Any]]
logger = logging.getLogger("personal_ai_assistant.task_worker")


def is_retryable_task_error(error: Exception) -> bool:
    """Avoid spending retries on invalid input, broken assumptions, or integrity conflicts."""
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
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds

    def _maintain_lease(self, context: TaskContext, stop_event: threading.Event) -> None:
        """Renew a running task lease while its handler is doing long-running work."""
        interval = max(0.05, min(30.0, self.lease_seconds / 3))
        while not stop_event.wait(interval):
            try:
                # Do not extend a lease after pause/cancel has been requested. If the
                # handler cannot reach a checkpoint, expiry lets the queue settle it.
                if context.control_status() != "running":
                    return
                task_manager.renew_lease(
                    context.task_id, context.worker_id, lease_seconds=self.lease_seconds
                )
            except TaskManagerError:
                # Ownership loss or a control-state transition ends heartbeat ownership.
                return
            except Exception:
                logger.exception("Task lease heartbeat failed for task %s", context.task_id)
                return

    def run_once(self) -> bool:
        """Claim and process one task. Return False only when no task is ready."""
        task = task_manager.claim_next_task(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            task_types=tuple(self.handlers),
        )
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
        heartbeat_thread = threading.Thread(
            target=self._maintain_lease,
            args=(context, heartbeat_stop),
            name=f"{self.worker_id}-lease-{task['id']}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
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
