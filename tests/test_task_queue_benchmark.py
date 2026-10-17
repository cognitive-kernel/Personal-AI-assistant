from app import db
from scripts.benchmark_task_queue import run_benchmark


def test_task_queue_benchmark_uses_isolated_stores_and_checks_completed_count(tmp_path):
    settings_path = str(tmp_path / "benchmark-settings.db")
    learning_path = str(tmp_path / "benchmark-learning.db")
    original_settings = db.defaults.settings_db_path
    original_learning = db.defaults.learning_db_path

    report = run_benchmark(
        task_count=12,
        producers=3,
        workers=4,
        settings_path=settings_path,
        learning_path=learning_path,
    )

    assert report["result"] == "passed"
    assert report["task_count_requested"] == 12
    assert report["task_count_enqueued"] == 12
    assert report["task_count_succeeded"] == 12
    assert report["producer_workers"] == 3
    assert report["consumer_workers"] == 4
    assert report["enqueue_tasks_per_second"] > 0
    assert report["claim_and_complete_tasks_per_second"] > 0
    assert report["sqlite_integrity"] == "ok"
    assert report["database_bytes"] > 0
    assert db.defaults.settings_db_path == original_settings
    assert db.defaults.learning_db_path == original_learning


def test_task_queue_benchmark_rejects_out_of_bounds_parameters(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="task_count"):
        run_benchmark(
            task_count=0,
            producers=1,
            workers=1,
            settings_path=str(tmp_path / "settings.db"),
            learning_path=str(tmp_path / "learning.db"),
        )
    with pytest.raises(ValueError, match="producers"):
        run_benchmark(
            task_count=1,
            producers=17,
            workers=1,
            settings_path=str(tmp_path / "settings.db"),
            learning_path=str(tmp_path / "learning.db"),
        )


def test_task_queue_benchmark_rejects_invalid_consumer_count(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="workers"):
        run_benchmark(
            task_count=1,
            producers=1,
            workers=0,
            settings_path=str(tmp_path / "settings.db"),
            learning_path=str(tmp_path / "learning.db"),
        )


def test_task_queue_benchmark_refuses_configured_application_databases(tmp_path, monkeypatch):
    import pytest

    live_settings = tmp_path / "live-settings.db"
    live_learning = tmp_path / "live-learning.db"
    live_settings.write_bytes(b"preserve settings")
    live_learning.write_bytes(b"preserve learning")
    monkeypatch.setattr(db.defaults, "settings_db_path", str(live_settings))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(live_learning))

    with pytest.raises(ValueError, match="configured database stores"):
        run_benchmark(
            task_count=1,
            producers=1,
            workers=1,
            settings_path=str(live_settings),
            learning_path=str(tmp_path / "benchmark-learning.db"),
        )

    assert live_settings.read_bytes() == b"preserve settings"
    assert live_learning.read_bytes() == b"preserve learning"


def test_task_queue_benchmark_refuses_same_file_for_both_stores(tmp_path):
    import pytest

    shared_path = str(tmp_path / "shared.db")
    with pytest.raises(ValueError, match="different files"):
        run_benchmark(
            task_count=1,
            producers=1,
            workers=1,
            settings_path=shared_path,
            learning_path=str(tmp_path / "." / "shared.db"),
        )
    assert not (tmp_path / "shared.db").exists()
