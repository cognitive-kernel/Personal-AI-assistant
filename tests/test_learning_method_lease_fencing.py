"""Method-gate transitions must be fenced by the current durable task lease."""

from datetime import datetime, timedelta, timezone

import pytest

from app import db, learning, learning_execution, learning_method_orchestrator, task_manager
from app.task_manager import TaskManagerError
from app.task_worker import TaskContext
from app.learning_topic_method_attempts import list_attempt_events


@pytest.fixture
def method_lease_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('lease-fencing', 'Lease fencing')"
        ).lastrowid)
        topic_ids = [
            int(connection.execute(
                """INSERT INTO learning_path_topics(path_id, topic, position, status)
                   VALUES (?, ?, ?, 'in_progress')""",
                (path_id, f"Topic {position}", position),
            ).lastrowid)
            for position in (1, 2)
        ]
    return topic_ids


def _reclaimed_context(monkeypatch):
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(task_manager, "_now", lambda: now)
    task = task_manager.create_task("learning.method.fence", {})
    first = task_manager.claim_next_task("same-worker", lease_seconds=5)
    now += timedelta(seconds=6)
    second = task_manager.claim_next_task("same-worker", lease_seconds=30)
    assert first["id"] == second["id"] == task["id"]
    stale = TaskContext(task_id=task["id"], worker_id="same-worker", lease_epoch=first["lease_epoch"])
    current = TaskContext(task_id=task["id"], worker_id="same-worker", lease_epoch=second["lease_epoch"])
    return stale, current


def _queue_method(topic_id, monkeypatch):
    monkeypatch.setattr(
        learning, "_enqueue_learning_job",
        lambda job_id: {"id": f"task-{job_id}"},
    )
    queued = learning_method_orchestrator.queue_initial_method(topic_id)
    job_id = int(queued["job"]["id"])
    domain_lease = learning_execution.claim_execution(job_id, "method-worker")
    assert domain_lease is not None
    learning_method_orchestrator.mark_method_running(job_id, execution_lease=domain_lease)
    return job_id, domain_lease


def test_stale_task_owner_cannot_pass_or_advance_topic(method_lease_store, monkeypatch):
    stale, current = _reclaimed_context(monkeypatch)
    topic_id = method_lease_store[0]
    job_id, _domain_lease = _queue_method(topic_id, monkeypatch)
    lesson_hash = "a" * 64
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', lesson='completed lesson' WHERE id=?",
            (job_id,),
        )

    with pytest.raises(TaskManagerError, match="epoch is stale"):
        learning_method_orchestrator.complete_method(
            job_id,
            score=95,
            evaluation_id=1,
            content_sha256=lesson_hash,
            diagnosis={"passed": True},
            task_context=stale,
        )

    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        topic = connection.execute(
            "SELECT status FROM learning_path_topics WHERE id=?", (topic_id,)
        ).fetchone()
    assert gate["state"] == "pending"
    assert gate["current_job_id"] == job_id
    assert topic["status"] == "in_progress"
    assert [event["status"] for event in list_attempt_events(topic_id)] == ["queued", "running"]

    result = learning_method_orchestrator.complete_method(
        job_id,
        score=95,
        evaluation_id=1,
        content_sha256=lesson_hash,
        diagnosis={"passed": True},
        task_context=current,
    )
    assert result["status"] == "passed"
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        topic = connection.execute(
            "SELECT status FROM learning_path_topics WHERE id=?", (topic_id,)
        ).fetchone()
    assert gate["state"] == "passed"
    assert topic["status"] == "completed"


def test_stale_task_owner_cannot_record_failure_or_create_fallback(method_lease_store, monkeypatch):
    stale, current = _reclaimed_context(monkeypatch)
    topic_id = method_lease_store[1]
    job_id, _domain_lease = _queue_method(topic_id, monkeypatch)
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='failed', error='provider timeout' WHERE id=?",
            (job_id,),
        )

    with pytest.raises(TaskManagerError, match="epoch is stale"):
        learning_method_orchestrator.record_generation_failure(
            job_id,
            reason="provider timeout",
            task_context=stale,
        )

    assert [event["status"] for event in list_attempt_events(topic_id)] == ["queued", "running"]
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
    assert gate["state"] == "pending"
    assert gate["current_job_id"] == job_id

    result = learning_method_orchestrator.record_generation_failure(
        job_id,
        reason="provider timeout",
        task_context=current,
    )
    assert result["method"] == "basic_learning"
    assert [event["status"] for event in list_attempt_events(topic_id)] == [
        "queued", "running", "failed", "queued"
    ]
