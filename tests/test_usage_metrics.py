from app import db, task_manager, usage_metrics


def test_model_usage_is_bound_to_active_task_and_stores_no_prompt_content(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    task = task_manager.create_task("learning.generate_lesson", {"job_id": 7, "secret": "must not be stored"})
    response = {
        "prompt_eval_count": 10, "eval_count": 5,
        "total_duration": 1000000, "load_duration": 100,
        "prompt_eval_duration": 200, "eval_duration": 300,
        "message": {"content": "private prompt output"},
    }
    assert usage_metrics.record_ollama_response(purpose="learning", model="local-model", response=response) is False
    with usage_metrics.capture_task(task["id"]):
        assert usage_metrics.current_task_id() == task["id"]
        assert usage_metrics.record_ollama_response(purpose="learning", model="local-model", response=response) is True
    assert usage_metrics.current_task_id() is None
    with db.learning_connection() as connection:
        row = connection.execute("SELECT * FROM model_usage_events WHERE task_id=?", (task["id"],)).fetchone()
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_duration_ns"] == 1000000
    assert "private prompt output" not in str(dict(row))
    assert "must not be stored" not in str(dict(row))


def test_model_usage_rejects_invalid_values_and_unbound_context(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    task = task_manager.create_task("test.task", {})
    with usage_metrics.capture_task(task["id"]):
        assert usage_metrics.record_ollama_response(purpose="unknown", model="model", response={"eval_count": 10}) is False
        assert usage_metrics.record_ollama_response(purpose="learning", model="model", response={"eval_count": -1, "prompt_eval_count": True}) is False
    with db.learning_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM model_usage_events").fetchone()[0]
    assert count == 0
