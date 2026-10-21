import sqlite3

from app import cloud_resources, db, main


def test_startup_rejects_unknown_settings_schema_before_starting_workers(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.db"
    learning_path = tmp_path / "learning.db"
    monkeypatch.setattr(main.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(main.defaults, "learning_db_path", str(learning_path))
    monkeypatch.setattr(
        main.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(main, "_task_runtimes", [])
    constructed = []

    class RuntimeStub:
        def __init__(self, *args, **kwargs):
            constructed.append("constructed")

        def start(self):
            constructed.append("started")

    monkeypatch.setattr(main, "TaskRuntime", RuntimeStub)
    with sqlite3.connect(settings_path) as connection:
        connection.execute("CREATE TABLE unrecognized_settings (value TEXT)")
        connection.execute("INSERT INTO unrecognized_settings(value) VALUES ('preserve')")

    main.startup()

    assert main._startup_storage_error == "untracked_storage_schema"

    assert constructed == []
    backups = list(tmp_path.glob("settings.db.pre-migration-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(settings_path) as connection:
        assert connection.execute(
            "SELECT value FROM unrecognized_settings"
        ).fetchone() == ("preserve",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
    assert not learning_path.exists()



def test_startup_reconciles_missing_learning_task_idempotently(tmp_path, monkeypatch):
    from app import learning, task_manager

    monkeypatch.setattr(main.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(main.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        main.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources, "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )
    monkeypatch.setattr(main, "_task_runtimes", [])
    db.init_databases()
    job = learning.create_job("startup recovery fixture")
    assert task_manager.get_task_by_dedupe_key(f"learning-job:{job['id']}") is None

    constructed = []

    class RuntimeStub:
        def __init__(self, handlers, **kwargs):
            self.handlers = handlers
            self.is_running = False
            self.start_count = 0
            constructed.append(self)

        def start(self):
            self.start_count += 1
            self.is_running = True

    monkeypatch.setattr(main, "TaskRuntime", RuntimeStub)
    main.startup()
    first_task = task_manager.get_task_by_dedupe_key(f"learning-job:{job['id']}")
    main.startup()
    second_task = task_manager.get_task_by_dedupe_key(f"learning-job:{job['id']}")

    assert first_task is not None
    assert first_task["task_type"] == "learning.run_job"
    assert first_task["payload"] == {"job_id": job["id"]}
    assert first_task["status"] == "queued"
    assert second_task["id"] == first_task["id"]
    assert len(task_manager.list_tasks(statuses=["queued"])) == 1
    assert len(constructed) >= 3
    assert all(runtime.start_count == 1 for runtime in constructed)
    assert {runtime.handlers.keys().__iter__().__next__() for runtime in constructed} >= {
        "research.answer", "learning.run_job", "cloud.discover_resources"
    }
