from __future__ import annotations

import pytest

from app import backup_scheduler, db, module_control, task_manager
from app.ai_learning.db import initialize_store as initialize_ai_learning_store


@pytest.fixture
def lifecycle_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    initialize_ai_learning_store()
    yield tmp_path
    backup_scheduler.stop_backup_scheduler(timeout=2)


def test_module_list_includes_independent_ai_learning_and_backup_owners(lifecycle_stores, monkeypatch):
    monkeypatch.setattr(db.defaults, "database_backup_interval_hours", 0)
    modules = {item["id"]: item for item in module_control.list_module_controls()}

    assert "ai-learning-browser" in modules
    assert "backup-scheduler" in modules
    assert modules["ai-learning-browser"]["state"] == "running"
    assert modules["backup-scheduler"]["state"] == "disabled"
    assert "start" in modules["backup-scheduler"]["disabled_actions"]
    assert "payload" not in str(modules)


def test_ai_learning_lifecycle_controls_drain_pause_resume_and_force_stop(lifecycle_stores):
    finished = module_control.control_module("ai-learning-browser", "finish")
    assert finished["state"] == "finished"
    assert finished["operation_status"] == "finished"

    draining = module_control.control_module("ai-learning-browser", "finish")
    assert draining["state"] == "finished"

    started = module_control.control_module("ai-learning-browser", "start")
    assert started["state"] == "running"

    paused = module_control.control_module("ai-learning-browser", "pause")
    assert paused["state"] == "paused"

    resumed = module_control.control_module("ai-learning-browser", "resume")
    assert resumed["state"] == "running"

    stopped = module_control.control_module("ai-learning-browser", "force_stop")
    assert stopped["state"] == "stopped"
    assert stopped["operation_status"] == "stopped"


def test_backup_scheduler_controls_are_safe_and_do_not_run_snapshot_immediately(
    lifecycle_stores, monkeypatch
):
    monkeypatch.setattr(db.defaults, "database_backup_interval_hours", 1)
    monkeypatch.setattr(db.defaults, "database_backup_dir", str(lifecycle_stores / "backups"))

    started = module_control.control_module("backup-scheduler", "start")
    assert started["state"] == "running"
    assert started["interval_hours"] == 1
    assert list((lifecycle_stores / "backups").iterdir()) == []

    paused = module_control.control_module("backup-scheduler", "pause")
    assert paused["state"] == "paused"
    assert backup_scheduler.get_backup_scheduler_status()["running"] is False

    resumed = module_control.control_module("backup-scheduler", "resume")
    assert resumed["state"] == "running"

    finished = module_control.control_module("backup-scheduler", "finish")
    assert finished["state"] == "finished"
    assert backup_scheduler.get_backup_scheduler_status()["running"] is False


def test_backup_scheduler_cannot_start_without_explicit_interval(lifecycle_stores, monkeypatch):
    monkeypatch.setattr(db.defaults, "database_backup_interval_hours", 0)
    with pytest.raises(module_control.ModuleControlError, match="DATABASE_BACKUP_INTERVAL_HOURS"):
        module_control.control_module("backup-scheduler", "start")
