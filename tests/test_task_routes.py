from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, learning, task_manager
from app.learning_tasks import enqueue_learning_task
from app.main import app
from app.task_routes import cancel_task as cancel_task_route


def test_task_api_lifecycle_and_filtering(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    initial_due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/tasks",
            json={
                "task_type": "learn.topic",
                "payload": {"topic": "API design"},
                "priority": 8,
                "max_attempts": 3,
                "scheduled_at": initial_due,
            },
        )
        assert response.status_code == 202
        created = response.json()
        task_id = created["id"]
        assert created["status"] == "queued"
        assert created["payload"] == {"topic": "API design"}

        filtered = client.get("/api/tasks", params={"statuses": "queued", "limit": 10})
        assert filtered.status_code == 200
        assert [task["id"] for task in filtered.json()["tasks"]] == [task_id]

        priority = client.patch(f"/api/tasks/{task_id}/priority", json={"priority": 12})
        assert priority.status_code == 200
        assert priority.json()["priority"] == 12

        due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        scheduled = client.patch(f"/api/tasks/{task_id}/schedule", json={"scheduled_at": due})
        assert scheduled.status_code == 200
        assert scheduled.json()["scheduled_at"].startswith(due[:19])

        paused = client.post(f"/api/tasks/{task_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        resumed = client.post(f"/api/tasks/{task_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "queued"
        assert resumed.json()["scheduled_at"] == scheduled.json()["scheduled_at"]

        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "cancelled"


def test_task_api_validation_and_missing_task(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        invalid = client.post("/api/tasks", json={"task_type": "", "payload": {}, "max_attempts": 0})
        assert invalid.status_code == 422

        missing = client.get("/api/tasks/999999")
        assert missing.status_code == 404

        invalid_filter = client.get("/api/tasks", params={"statuses": "unknown"})
        assert invalid_filter.status_code == 422

        missing_control = client.post("/api/tasks/999999/cancel")
        assert missing_control.status_code == 404


def test_task_api_rejects_control_changes_for_running_task(tmp_path, monkeypatch):
    from app import task_manager

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post("/api/tasks", json={"task_type": "running-task", "payload": {}}).json()
        claimed = task_manager.claim_next_task("api-test-worker")
        assert claimed["id"] == created["id"]

        priority = client.patch(
            f"/api/tasks/{created['id']}/priority", json={"priority": 100}
        )
        assert priority.status_code == 409

        reschedule = client.patch(
            f"/api/tasks/{created['id']}/schedule",
            json={"scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        )
        assert reschedule.status_code == 409

        cancelled = client.post(f"/api/tasks/{created['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancel_requested"


def test_task_api_rejects_non_loopback_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/tasks")
        assert response.status_code == 403



def test_generic_task_cancel_updates_queued_learning_job(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    job = learning.create_job("Cancel through generic task API")
    task = enqueue_learning_task(job["id"])

    cancelled = cancel_task_route(task["id"])

    assert cancelled["status"] == "cancelled"
    assert learning.get_job(job["id"])["status"] == "cancelled"


def test_generic_task_cancel_updates_running_learning_job(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    job = learning.create_job("Cancel running learning through generic task API")
    task = enqueue_learning_task(job["id"])
    claimed = task_manager.claim_next_task("route-cancel-test")
    assert claimed["id"] == task["id"]

    cancelled = cancel_task_route(task["id"])

    assert cancelled["status"] == "cancel_requested"
    assert learning.get_job(job["id"])["status"] == "cancelled"



def test_fastapi_runtime_executes_durable_learning_task_and_shuts_down(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    def fake_run_job(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='completed', error=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", fake_run_job)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/learning/jobs", json={"topic": "Runtime lifecycle integration"}
        )
        assert created.status_code == 202
        job = created.json()
        deadline = time.monotonic() + 5.0
        task = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/tasks/{job['task_id']}")
            assert response.status_code == 200
            task = response.json()
            if task["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert task is not None
        assert task["status"] == "succeeded"
        assert learning.get_job(job["id"])["status"] == "completed"


def test_startup_recovers_running_learning_record_without_task(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    job = learning.create_job("Recover running record on startup")
    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='running' WHERE id=?", (job["id"],))

    def fake_run_job(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='completed', error=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", fake_run_job)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        deadline = time.monotonic() + 5.0
        task = None
        while time.monotonic() < deadline:
            task = task_manager.get_task_by_dedupe_key(f"learning-job:{job['id']}")
            if task and task["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert task is not None
        assert task["status"] == "succeeded"
        assert learning.get_job(job["id"])["status"] == "completed"
