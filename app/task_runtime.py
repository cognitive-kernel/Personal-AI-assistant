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
        restrict_task_types: bool = False,
        exclude_task_types: tuple[str, ...] | None = None,
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
        TaskWorker(
            f"{worker_prefix.strip()}-validation", handlers,
            lease_seconds=lease_seconds, exclude_task_types=exclude_task_types,
        )
        self.handlers = dict(handlers)
        self.worker_count = worker_count
        self.worker_prefix = worker_prefix.strip()
        self.poll_interval = float(poll_interval)
        self.lease_seconds = lease_seconds
        self.maintenance_callback = maintenance_callback
        self.maintenance_interval = float(maintenance_interval)
        self.restrict_task_types = bool(restrict_task_types)
        self.exclude_task_types = exclude_task_types
        self._maintenance_ready = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _managed_threads(self) -> list[threading.Thread]:
        threads = list(self._threads)
        if self._maintenance_thread is not None:
            threads.append(self._maintenance_thread)
        return threads

    @property
    def is_running(self) -> bool:
        """Return whether any managed thread is still alive."""
        return any(thread.is_alive() for thread in self._managed_threads())

    @property
    def is_healthy(self) -> bool:
        """Return whether every configured worker and maintenance thread is alive."""
        if len(self._threads) != self.worker_count or not all(
            thread.is_alive() for thread in self._threads
        ):
            return False
        if self.maintenance_callback is None:
            return self._maintenance_thread is None
        return (
            self._maintenance_thread is not None
            and self._maintenance_thread.is_alive()
        )

    def start(self) -> None:
        """Start the configured workers; only a fully healthy start is idempotent."""
        with self._lock:
            if self.is_healthy:
                return
            # A partial runtime must be stopped before replacement threads start.
            # Otherwise a surviving worker could overlap with its replacement and
            # claim work under the same worker identity.
            active_threads = [
                thread for thread in self._managed_threads() if thread.is_alive()
            ]
            if active_threads:
                self._stop_event.set()
                deadline = time.monotonic() + 10.0
                for thread in active_threads:
                    thread.join(max(0.0, deadline - time.monotonic()))
                if any(thread.is_alive() for thread in active_threads):
                    raise RuntimeError(
                        f"Cannot restart task runtime {self.worker_prefix}: "
                        "previous worker threads did not stop."
                    )
            self._threads = []
            self._maintenance_thread = None
            self._stop_event.clear()
            self._maintenance_ready.clear()
            try:
                for index in range(self.worker_count):
                    worker = TaskWorker(
                        f"{self.worker_prefix}-{index + 1}",
                        self.handlers,
                        lease_seconds=self.lease_seconds,
                        restrict_task_types=self.restrict_task_types,
                        exclude_task_types=self.exclude_task_types,
                    )
                    thread = threading.Thread(
                        target=self._run_worker,
                        args=(worker,),
                        name=f"{self.worker_prefix}-{index + 1}",
                        daemon=True,
                    )
                    self._threads.append(thread)
                    thread.start()
                if self.maintenance_callback is not None:
                    self._maintenance_thread = threading.Thread(
                        target=self._run_maintenance,
                        name=f"{self.worker_prefix}-maintenance",
                        daemon=True,
                    )
                    self._maintenance_thread.start()
            except Exception:
                # Thread creation can fail partway through startup (for example,
                # when the OS process/thread limit is reached). Roll back every
                # thread that actually started before exposing the failure so a
                # later startup retry cannot silently leave duplicate workers.
                self._stop_event.set()
                started = [thread for thread in self._threads if thread.is_alive()]
                if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
                    started.append(self._maintenance_thread)
                deadline = time.monotonic() + 5.0
                for thread in started:
                    thread.join(max(0.0, deadline - time.monotonic()))
                self._threads = [thread for thread in self._threads if thread.is_alive()]
                if self._maintenance_thread is not None and not self._maintenance_thread.is_alive():
                    self._maintenance_thread = None
                logger.exception(
                    "Task runtime %s failed to start completely; started threads were asked to stop.",
                    self.worker_prefix,
                )
                raise

    def _run_maintenance(self) -> None:
        callback = self.maintenance_callback
        if callback is None:
            return
        while not self._stop_event.is_set():
            # Do not run maintenance until at least one worker poll has completed.
            # claim_next_task() reconciles expired leases before this event is set.
            if not self._maintenance_ready.wait(timeout=self.poll_interval):
                continue
            if self._stop_event.is_set():
                return
            try:
                callback()
            except Exception:
                logger.exception("Task maintenance callback failed; it will retry after the interval.")
            if self._stop_event.wait(self.maintenance_interval):
                return

    def _run_worker(self, worker: TaskWorker) -> None:
        while not self._stop_event.is_set():
            try:
                processed = worker.run_once()
            except Exception:
                logger.exception("Task worker loop failed; it will retry after the poll interval.")
                processed = False
            self._maintenance_ready.set()
            if not processed:
                self._stop_event.wait(self.poll_interval)

    def stop(self, *, timeout: float = 10.0) -> bool:
        """Request shutdown and wait up to timeout seconds for active handlers."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a non-negative number.")
        with self._lock:
            self._stop_event.set()
            threads = list(self._threads)
            if self._maintenance_thread is not None:
                threads.append(self._maintenance_thread)
        # Join against a shared monotonic deadline so total shutdown time is bounded.
        end = time.monotonic() + timeout
        for thread in threads:
            remaining = max(0.0, end - time.monotonic())
            thread.join(remaining)
        stopped = not self.is_running
        if not stopped:
            logger.warning("Task runtime shutdown timed out; worker or maintenance threads are still active.")
        return stopped
