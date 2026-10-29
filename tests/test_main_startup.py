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
            self.worker_prefix = kwargs.get("worker_prefix", "stub-worker")
            self.is_running = False
            self.start_count = 0
            self.stop_count = 0
            constructed.append(self)

        def start(self):
            self.start_count += 1
            self.is_running = True

        def stop(self, timeout=10.0):
            self.stop_count += 1
            self.is_running = False
            return True

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
    queued_learning_tasks = [
        task for task in task_manager.list_tasks(statuses=["queued"])
        if task["task_type"] == "learning.run_job"
    ]
    assert len(queued_learning_tasks) == 1
    assert len(constructed) >= 3
    assert all(runtime.start_count == 1 for runtime in constructed)
    assert {runtime.handlers.keys().__iter__().__next__() for runtime in constructed} >= {
        "research.answer", "learning.run_job", "cloud.discover_resources"
    }

    # A partially stopped runtime set must be rebuilt rather than mistaken for
    # a healthy startup; all surviving workers stop before replacements start.
    previous_runtimes = list(constructed)
    previous_count = len(constructed)
    previous_runtimes[0].is_running = False
    main.startup()

    assert len(constructed) > previous_count
    assert main._task_runtimes != previous_runtimes
    assert all(not runtime.is_running for runtime in previous_runtimes)
    assert all(runtime.stop_count == 1 for runtime in previous_runtimes if runtime is not previous_runtimes[0])
    assert all(runtime.is_running for runtime in main._task_runtimes)
