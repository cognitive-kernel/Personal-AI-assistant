import pytest
from fastapi.testclient import TestClient

from app import db, learning, task_manager
from app.learning_tasks import (
    LEARNING_TASK_TYPE,
    handle_learning_job,
    list_learning_escalations,
    resolve_learning_escalation,
)
from app.main import app
from app.task_worker import RetryableTaskError, TaskContext


@pytest.fixture
def escalation_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def _claimed_task(job_id, *, max_attempts=3):
    task = task_manager.create_task(
        LEARNING_TASK_TYPE,
        {"job_id": job_id},
        max_attempts=max_attempts,
        dedupe_key=f"escalation-test:{job_id}",
    )
    claimed = task_manager.claim_next_task("escalation-worker")
    assert claimed["id"] == task["id"]
    return task, TaskContext(task_id=task["id"], worker_id="escalation-worker")


def test_retry_exhaustion_creates_one_pending_learning_escalation(
    escalation_store, monkeypatch
):
    job = learning.create_job("Transient learning error")
    task, context = _claimed_task(job["id"], max_attempts=2)
    with db.learning_connection() as conn:
        conn.execute("UPDATE task_queue SET attempts=2 WHERE id=?", (task["id"],))

    def fail(_job_id):
        raise learning.LearningError("temporary source failure") from RuntimeError("network")

    monkeypatch.setattr(learning, "run_job", fail)
    with pytest.raises(RetryableTaskError):
        handle_learning_job({"job_id": job["id"]}, None, context)

    items = list_learning_escalations()
    assert len(items) == 1
    assert items[0]["task_id"] == task["id"]
    assert items[0]["job_id"] == job["id"]
    assert items[0]["reason"] == "attempts_exhausted"
    assert items[0]["attempts"] == items[0]["max_attempts"] == 2
    assert items[0]["status"] == "pending"

    # Repeated delivery must not create a second escalation for the same task.
    from app.learning_tasks import _record_learning_escalation
    _record_learning_escalation(task["id"], job["id"], "attempts_exhausted", "duplicate")
    assert len(list_learning_escalations()) == 1


def test_permanent_learning_failure_is_escalated_without_retry(
    escalation_store, monkeypatch
):
    job = learning.create_job("Invalid learning configuration")
    task, context = _claimed_task(job["id"], max_attempts=3)

    def fail(_job_id):
        raise learning.LearningError("invalid configuration") from ValueError("bad setting")

    monkeypatch.setattr(learning, "run_job", fail)
    with pytest.raises(ValueError, match="configuration or policy is invalid"):
        handle_learning_job({"job_id": job["id"]}, None, context)

    items = list_learning_escalations()
    assert len(items) == 1
    assert items[0]["reason"] == "permanent_failure"
    assert items[0]["attempts"] == 1
    assert items[0]["max_attempts"] == 3


def test_quality_rejected_lesson_enters_human_review_queue(
    escalation_store, monkeypatch
):
    job = learning.create_job("Lesson rejected by quality gate")
    task, context = _claimed_task(job["id"])

    def reject(job_id):
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE learning_jobs SET status='rejected', error='quality gate rejected lesson' WHERE id=?",
                (job_id,),
            )

    monkeypatch.setattr(learning, "run_job", reject)
    result = handle_learning_job({"job_id": job["id"]}, None, context)

    assert result["learning_status"] == "rejected"
    items = list_learning_escalations()
    assert len(items) == 1
    assert items[0]["task_id"] == task["id"]
    assert items[0]["reason"] == "quality_rejected"
    assert "quality gate" in items[0]["error_summary"]


def test_learning_escalations_can_be_resolved_idempotently(escalation_store):
    job = learning.create_job("Review note persistence")
    task, _context = _claimed_task(job["id"])
    with db.learning_connection() as conn:
        conn.execute("UPDATE task_queue SET status='failed' WHERE id=?", (task["id"],))
        conn.execute("UPDATE learning_jobs SET status='failed' WHERE id=?", (job["id"],))
        conn.execute(
            """INSERT INTO learning_escalations(
                   task_id, job_id, reason, attempts, max_attempts, error_summary
               ) VALUES (?, ?, 'permanent_failure', 1, 3, 'configuration error')""",
            (task["id"], job["id"]),
        )
        escalation_id = conn.execute(
            "SELECT id FROM learning_escalations WHERE task_id=?", (task["id"],)
        ).fetchone()["id"]

    resolved = resolve_learning_escalation(escalation_id, "reviewed", "Checked model configuration.")
    repeated = resolve_learning_escalation(escalation_id, "reviewed", "Checked model configuration.")
    assert resolved["status"] == "reviewed"
    assert resolved["review_note"] == "Checked model configuration."
    assert repeated["status"] == "reviewed"
    assert list_learning_escalations(status="pending") == []
    assert len(list_learning_escalations(status="reviewed")) == 1

    with pytest.raises(ValueError, match="already been resolved"):
        resolve_learning_escalation(escalation_id, "dismissed", "Conflicting decision.")


def test_learning_escalation_api_lists_and_resolves_items(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    monkeypatch.setattr(
        "app.cloud_resources.ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )

    db.init_databases()
    task_manager.initialize_task_store()
    job = learning.create_job("API escalation review")
    task = task_manager.create_task(
        LEARNING_TASK_TYPE, {"job_id": job["id"]},
        max_attempts=2, dedupe_key=f"api-escalation:{job['id']}",
    )
    with db.learning_connection() as conn:
        conn.execute("UPDATE task_queue SET status='failed', attempts=2 WHERE id=?", (task["id"],))
        conn.execute("UPDATE learning_jobs SET status='failed' WHERE id=?", (job["id"],))
        conn.execute(
            """INSERT INTO learning_escalations(
                   task_id, job_id, reason, attempts, max_attempts, error_summary
               ) VALUES (?, ?, 'attempts_exhausted', 2, 2, 'retries exhausted')""",
            (task["id"], job["id"]),
        )
        escalation_id = conn.execute(
            "SELECT id FROM learning_escalations WHERE task_id=?", (task["id"],)
        ).fetchone()["id"]

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        listed = client.get("/api/learning/escalations")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["escalations"][0]["id"] == escalation_id

        updated = client.patch(
            f"/api/learning/escalations/{escalation_id}",
            json={"status": "reviewed", "review_note": "Investigated the retry failure."},
        )
        assert updated.status_code == 200
        assert updated.json()["escalation"]["status"] == "reviewed"

        missing = client.patch(
            "/api/learning/escalations/999999",
            json={"status": "reviewed", "review_note": ""},
        )
        assert missing.status_code == 404
