import pytest
from fastapi import HTTPException

from app import db, learning, learning_practical_routes, sandbox_tasks
from app.learning_practical_routes import PracticalSubmission


def _setup(tmp_path, monkeypatch, topic="Python JSON processing"):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job(topic)
    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='completed' WHERE id=?", (job["id"],))
    return job["id"]


def test_exercise_catalog_is_filtered_to_completed_relevant_lessons(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch)

    result = learning_practical_routes.list_practical_exercises(job_id=job_id)
    assert {item["id"] for item in result["exercises"]} == {"python-json-sum-v1", "python-json-unique-v1"}

    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET topic='History' WHERE id=?", (job_id,))
    assert learning_practical_routes.list_practical_exercises(job_id=job_id)["exercises"] == []


def test_practical_submit_requires_consent_and_configured_sandbox(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        learning_practical_routes.sandbox_consent, "get_consent",
        lambda: {"enabled": False},
    )
    monkeypatch.setattr(learning_practical_routes, "_sandbox_available", lambda: True)

    with pytest.raises(HTTPException) as exc:
        learning_practical_routes.submit_practical_exercise(
            "python-json-sum-v1",
            PracticalSubmission(job_id=job_id, source_code="print(0)"),
        )
    assert exc.value.status_code == 409


def test_practical_submit_queues_consent_gated_sandbox_task(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        learning_practical_routes.sandbox_consent, "get_consent",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(learning_practical_routes, "_sandbox_available", lambda: True)
    captured = {}

    def fake_create_task(task_type, payload, **kwargs):
        captured.update({"task_type": task_type, "payload": payload, **kwargs})
        return {"id": 123, "status": "queued"}

    monkeypatch.setattr(learning_practical_routes.task_manager, "create_task", fake_create_task)
    result = learning_practical_routes.submit_practical_exercise(
        "python-json-sum-v1",
        PracticalSubmission(
            job_id=job_id,
            source_code="import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps(sum(data['values'])))",
        ),
    )

    assert result["task"]["id"] == 123
    assert captured["task_type"] == sandbox_tasks.SANDBOX_TASK_TYPE
    assert captured["payload"]["learning_practical"] == {
        "exercise_id": "python-json-sum-v1", "job_id": job_id
    }
    assert captured["max_attempts"] == 1
