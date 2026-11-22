"""Opt-in periodic verified backups.

The scheduler is disabled by default. When explicitly enabled through
DATABASE_BACKUP_INTERVAL_HOURS, it creates a complete three-store bundle after
each interval and never performs restore, cutover, or deletion.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import defaults
from .db_backup import backup_configured_databases

logger = logging.getLogger("personal_ai_assistant.backup_scheduler")


class BackupScheduler:
    def __init__(self, interval_hours: int, backup_root: str | Path) -> None:
        if isinstance(interval_hours, bool) or not isinstance(interval_hours, int) or not 1 <= interval_hours <= 168:
            raise ValueError("Backup interval must be between 1 and 168 hours.")
        self._interval_seconds = interval_hours * 3600
        self._backup_root = Path(backup_root).expanduser().resolve()
        self._stop_event = threading.Event()
        self._backup_active = threading.Event()
        self._state_lock = threading.Lock()
        self._last_backup_id: str | None = None
        self._last_backup_status = "never"
        self._last_error: str | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="database-backup-scheduler",
            daemon=True,
        )

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "running": self.is_alive,
                "backup_active": self._backup_active.is_set(),
                "last_backup_id": self._last_backup_id,
                "last_backup_status": self._last_backup_status,
                "last_error": self._last_error,
                "interval_seconds": self._interval_seconds,
            }

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))
        return not self._thread.is_alive()

    def _run(self) -> None:
        # Avoid surprising disk writes during startup; wait one full interval.
        while not self._stop_event.wait(self._interval_seconds):
            backup_id = datetime.now(timezone.utc).strftime("snapshot-%Y%m%dT%H%M%S%fZ")
            destination = self._backup_root / backup_id
            self._backup_active.set()
            with self._state_lock:
                self._last_backup_id = backup_id
                self._last_backup_status = "running"
                self._last_error = None
            try:
                manifest = backup_configured_databases(destination)
                roles = sorted(manifest.get("databases", {}).keys())
                if roles != ["cloud_resources", "learning", "settings"]:
                    raise RuntimeError("Scheduled backup did not include all configured stores.")
                with self._state_lock:
                    self._last_backup_status = "completed"
                logger.info("Scheduled database backup completed: %s", backup_id)
            except Exception as exc:
                # A scheduled backup failure must not crash the application.
                with self._state_lock:
                    self._last_backup_status = "failed"
                    self._last_error = type(exc).__name__
                logger.exception("Scheduled database backup failed.")
            finally:
                self._backup_active.clear()


_scheduler: BackupScheduler | None = None
_scheduler_lock = threading.Lock()



def get_backup_scheduler_status() -> dict[str, object]:
    """Return scheduler state without exposing backup paths or database contents."""
    interval = defaults.database_backup_interval_hours
    with _scheduler_lock:
        current = _scheduler
        running = current is not None and current.is_alive
        details = current.status() if current is not None else {
            "running": False,
            "backup_active": False,
            "last_backup_id": None,
            "last_backup_status": "never",
            "last_error": None,
            "interval_seconds": interval * 3600 if interval else 0,
        }
    return {
        "configured": interval > 0,
        "interval_hours": interval,
        "state": "running" if running else ("stopped" if interval > 0 else "disabled"),
        **details,
    }


def start_backup_scheduler(interval_hours: int | None = None) -> bool:
    """Start the scheduler only when an interval is explicitly configured."""
    global _scheduler
    interval = defaults.database_backup_interval_hours if interval_hours is None else interval_hours
    if interval == 0:
        return False
    candidate = BackupScheduler(interval, defaults.database_backup_dir)
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.is_alive:
            return False
        _scheduler = candidate
        _scheduler.start()
    logger.info("Periodic database backups enabled with interval=%s hours.", interval)
    return True


def stop_backup_scheduler(timeout: float = 5.0) -> bool:
    """Stop the periodic scheduler; an in-progress SQLite snapshot may finish first."""
    global _scheduler
    with _scheduler_lock:
        current = _scheduler
    if current is None:
        return True
    stopped = current.stop(timeout=timeout)
    with _scheduler_lock:
        if stopped and _scheduler is current:
            _scheduler = None
    if not stopped:
        logger.warning("Database backup scheduler did not stop before the shutdown deadline.")
    return stopped
