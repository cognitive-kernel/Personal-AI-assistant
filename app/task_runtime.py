"""Managed polling runtime for the allow-listed task dispatcher."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .task_worker import TaskHandler, TaskWorker

logger = logging.getLogger("personal_ai_assistant.task_runtime")


class TaskRuntime:
    """Own worker-thread lifecycle without coupling the scheduler to FastAPI."""

    def __init__(
        self,
        handlers: dict[str, TaskHandler],
        *,
        worker_count: int = 1,
        worker_prefix: str = "task-worker",
        poll_interval: float = 0.5,
        lease_seconds: int = 300,
        maintenance_callback: Callable[[], None] | None = None,
        maintenance_interval: float = 30.0,
    ) -> None:
        if isinstance(worker_count, bool) or not isinstance(worker_count, int) or not 1 <= worker_count <= 8:
            raise ValueError("worker_count must be an integer between 1 and 8.")
        if not isinstance(worker_prefix, str) or not worker_prefix.strip():
            raise ValueError("worker_prefix must be a non-empty string.")
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or not 0.05 <= poll_interval <= 60:
            raise ValueError("poll_interval must be between 0.05 and 60 seconds.")
        if maintenance_callback is not None and not callable(maintenance_callback):
            raise ValueError("maintenance_callback must be callable when provided.")
        if isinstance(maintenance_interval, bool) or not isinstance(maintenance_interval, (int, float)) or not 1 <= maintenance_interval <= 3600:
            raise ValueError("maintenance_interval must be between 1 and 3600 seconds.")
        # Validate the registry and lease configuration before any threads start.
        TaskWorker(f"{worker_prefix.strip()}-validation", handlers, lease_seconds=lease_seconds)
        self.handlers = dict(handlers)
        self.worker_count = worker_count
        self.worker_prefix = worker_prefix.strip()
        self.poll_interval = float(poll_interval)
        self.lease_seconds = lease_seconds
        self.maintenance_callback = maintenance_callback
        self.maintenance_interval = float(maintenance_interval)
        self._last_maintenance_at = 0.0
        self._maintenance_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def start(self) -> None:
        """Start the configured workers; repeated start calls are idempotent."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._threads = []
            for index in range(self.worker_count):
                worker = TaskWorker(
                    f"{self.worker_prefix}-{index + 1}",
                    self.handlers,
                    lease_seconds=self.lease_seconds,
                )
                thread = threading.Thread(
                    target=self._run_worker,
                    args=(worker,),
                    name=f"{self.worker_prefix}-{index + 1}",
                    daemon=True,
                )
                self._threads.append(thread)
                thread.start()

    def _run_maintenance_if_due(self) -> None:
        callback = self.maintenance_callback
        if callback is None:
            return
        with self._maintenance_lock:
            now = time.monotonic()
            if now - self._last_maintenance_at < self.maintenance_interval:
                return
            # Reserve the interval before invoking external maintenance so worker
            # threads cannot run the same cleanup concurrently.
            self._last_maintenance_at = now
            try:
                callback()
            except Exception:
                logger.exception("Task maintenance callback failed; it will retry after the interval.")

    def _run_worker(self, worker: TaskWorker) -> None:
        while not self._stop_event.is_set():
            try:
                processed = worker.run_once()
            except Exception:
                logger.exception("Task worker loop failed; it will retry after the poll interval.")
                processed = False
            self._run_maintenance_if_due()
            if not processed:
                self._stop_event.wait(self.poll_interval)

    def stop(self, *, timeout: float = 10.0) -> bool:
        """Request shutdown and wait up to timeout seconds for active handlers."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a non-negative number.")
        with self._lock:
            self._stop_event.set()
            threads = list(self._threads)
        # Join against a shared monotonic deadline so total shutdown time is bounded.
        end = time.monotonic() + timeout
        for thread in threads:
            remaining = max(0.0, end - time.monotonic())
            thread.join(remaining)
        stopped = not self.is_running
        if not stopped:
            logger.warning("Task runtime shutdown timed out; one or more handlers are still active.")
        return stopped
