import logging
import threading

import pytest

from app import backup_scheduler
from app.config import Defaults, defaults


class _FakeWait:
    def __init__(self, results):
        self.results = iter(results)

    def wait(self, _timeout):
        return next(self.results, True)

    def set(self):
        pass


def test_backup_scheduler_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(defaults, "database_backup_interval_hours", 0)
    monkeypatch.setattr(defaults, "database_backup_dir", str(tmp_path))
    assert backup_scheduler.start_backup_scheduler() is False
    assert backup_scheduler.stop_backup_scheduler() is True
    assert list(tmp_path.iterdir()) == []


def test_backup_scheduler_starts_and_stops_without_waiting_for_first_backup(monkeypatch, tmp_path):
    monkeypatch.setattr(defaults, "database_backup_interval_hours", 1)
    monkeypatch.setattr(defaults, "database_backup_dir", str(tmp_path))
    assert backup_scheduler.start_backup_scheduler() is True
    try:
        assert backup_scheduler.start_backup_scheduler() is False
    finally:
        assert backup_scheduler.stop_backup_scheduler(timeout=1) is True
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("interval", [True, -1, 169, "24"])
def test_backup_scheduler_rejects_invalid_intervals(interval, tmp_path):
    with pytest.raises(ValueError, match="between 1 and 168"):
        backup_scheduler.BackupScheduler(interval, tmp_path)


def test_backup_scheduler_creates_complete_bundle_after_interval(monkeypatch, tmp_path):
    scheduler = backup_scheduler.BackupScheduler(1, tmp_path)
    scheduler._stop_event = _FakeWait([False, True])
    calls = []

    def fake_backup(destination):
        calls.append(destination)
        return {"databases": {"settings": {}, "learning": {}, "cloud_resources": {}}}

    monkeypatch.setattr(backup_scheduler, "backup_configured_databases", fake_backup)
    scheduler._run()
    assert len(calls) == 1
    assert calls[0].parent == tmp_path.resolve()
    assert calls[0].name.startswith("snapshot-")
    assert not calls[0].exists()


def test_backup_scheduler_logs_failure_and_keeps_running(monkeypatch, tmp_path, caplog):
    scheduler = backup_scheduler.BackupScheduler(1, tmp_path)
    scheduler._stop_event = _FakeWait([False, False, True])
    calls = []

    def fake_backup(destination):
        calls.append(destination)
        if len(calls) == 1:
            raise OSError("simulated disk failure")
        return {"databases": {"settings": {}, "learning": {}, "cloud_resources": {}}}

    monkeypatch.setattr(backup_scheduler, "backup_configured_databases", fake_backup)
    with caplog.at_level(logging.ERROR):
        scheduler._run()
    assert len(calls) == 2
    assert "Scheduled database backup failed" in caplog.text


def test_backup_interval_config_validation():
    assert Defaults(database_backup_interval_hours=0).database_backup_interval_hours == 0
    assert Defaults(database_backup_interval_hours=24).database_backup_interval_hours == 24
    with pytest.raises(ValueError, match="must be 0"):
        Defaults(database_backup_interval_hours=-1)
    with pytest.raises(ValueError, match="must be 0"):
        Defaults(database_backup_interval_hours=169)
