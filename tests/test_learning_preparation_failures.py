import pytest

from app import db, learning


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


@pytest.mark.parametrize("failure_point", ["source_discovery", "verified_context"])
def test_pre_inference_failure_marks_learning_job_failed(
    learning_store, monkeypatch, failure_point
):
    job = learning.create_job(
        "Python lists",
        source_text="User-provided reference text" if failure_point == "verified_context" else None,
    )

    if failure_point == "source_discovery":
        def fail_discovery(*_args, **_kwargs):
            raise OSError("simulated source storage failure")

        monkeypatch.setattr(learning, "_discover_and_store_sources", fail_discovery)
    else:
        monkeypatch.setattr(
            learning,
            "_verified_factual_context_for_topic",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated retrieval failure")),
        )

    with pytest.raises(learning.LearningError, match="preparation failed"):
        learning.run_job(job["id"])

    finished = learning.get_job(job["id"])
    assert finished["status"] == "failed"
    assert finished["error"]
    assert "simulated" not in finished["error"]
