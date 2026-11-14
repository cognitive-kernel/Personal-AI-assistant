import concurrent.futures

import pytest

from app import db, learning, learning_execution, learning_method_orchestrator, task_manager
from app.task_worker import TaskContext
from app.task_ownership import LEARNING_JOB_TASK_TYPE
from app.learning_topic_method_attempts import list_attempt_events


@pytest.fixture
def method_job_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('method-jobs', 'Method jobs')"
        ).lastrowid)
        topic_id = int(connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, 'Research adapters', 1, 'in_progress')""",
            (path_id,),
        ).lastrowid)
    return topic_id

def _learning_task_context(job_id: int) -> TaskContext:
    task = task_manager.create_task(
        LEARNING_JOB_TASK_TYPE, {"job_id": job_id}, max_attempts=3
    )
    claimed = task_manager.claim_next_task(f"method-test-{job_id}", lease_seconds=300)
    assert claimed is not None and claimed["id"] == task["id"]
    return TaskContext(
        task_id=task["id"],
        worker_id=claimed["lease_owner"],
        lease_epoch=claimed["lease_epoch"],
    )



def test_learning_job_execution_epoch_fences_stale_workers(method_job_store):
    topic_id = method_job_store
    job = learning.create_job(
        "Research adapters",
        path_topic_id=topic_id,
        learning_method="ai_learning",
        method_attempt_key=f"topic:{topic_id}:ai_learning:1",
    )
    first = learning_execution.claim_execution(job["id"], "worker-a")
    assert first is not None
    learning_execution.assert_current(first)

    # Simulate durable task recovery: the new owner requeues the domain job,
    # then claims a strictly newer execution epoch.
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='queued' WHERE id=?",
            (job["id"],),
        )
    second = learning_execution.claim_execution(job["id"], "worker-b")
    assert second is not None
    assert second.epoch == first.epoch + 1

    with pytest.raises(learning_execution.LearningExecutionLeaseLostError):
        learning_execution.assert_current(first)
    assert learning_execution.fail_if_current(first, "stale worker failure") is False
    learning_execution.assert_current(second)
    current = learning.get_job(job["id"])
    assert current["status"] == "running"
    assert current["execution_owner"] == "worker-b"
    assert current["execution_epoch"] == second.epoch


def test_learning_job_execution_claim_is_atomic_under_concurrency(method_job_store):
    topic_id = method_job_store
    job = learning.create_job(
        "Research adapters",
        path_topic_id=topic_id,
        learning_method="ai_learning",
        method_attempt_key=f"topic:{topic_id}:ai_learning:1",
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(
            lambda index: learning_execution.claim_execution(job["id"], f"worker-{index}"),
            range(8),
        ))
    owners = [claim for claim in claims if claim is not None]
    assert len(owners) == 1
    learning_execution.assert_current(owners[0])


def test_method_job_creation_is_idempotent_and_versioned(method_job_store):
    topic_id = method_job_store
    first = learning.create_job(
        "Research adapters",
        source_text="Official source material for this method attempt.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )
    replay = learning.create_job(
        "Research adapters",
        source_text="Official source material for this method attempt.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )

    assert first["id"] == replay["id"]
    assert first["learning_method"] == "topic_learning"
    assert first["source_kind"] == "researched_topic_course"
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE method_attempt_key=?",
            (f"topic:{topic_id}:topic_learning:1",),
        ).fetchone()[0]
        evidence = connection.execute(
            "SELECT evidence_type, source_ref FROM learning_evidence WHERE job_id=?",
            (first["id"],),
        ).fetchone()
    assert count == 1
    assert evidence["evidence_type"] == "research_context"
    assert evidence["source_ref"] == "learning-path-research"


def test_method_job_key_cannot_be_reused_for_different_content(method_job_store):
    topic_id = method_job_store
    learning.create_job(
        "Research adapters",
        source_text="First source bundle.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )
    with pytest.raises(ValueError, match="different source text"):
        learning.create_job(
            "Research adapters",
            source_text="A different source bundle.",
            path_topic_id=topic_id,
            learning_method="topic_learning",
            method_attempt_key=f"topic:{topic_id}:topic_learning:1",
        )


def test_concurrent_method_job_creation_creates_one_job(method_job_store):
    topic_id = method_job_store
    key = f"topic:{topic_id}:basic_learning:1"

    def create():
        return learning.create_job(
            "Research adapters",
            path_topic_id=topic_id,
            learning_method="basic_learning",
            method_attempt_key=key,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _index: create(), range(8)))

    assert len({job["id"] for job in jobs}) == 1
    with db.learning_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE method_attempt_key=?",
            (key,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"learning_method": "experience_system"},
        {"method_attempt_key": ""},
        {"method_attempt_key": "not-allowed-without-topic", "path_topic_id": None},
        {"method_attempt_key": "not-allowed-with-correction", "correction_request_key": "correction"},
    ],
)
def test_method_job_contract_rejects_invalid_identity(method_job_store, kwargs):
    values = {"topic": "Research adapters", "path_topic_id": method_job_store}
    values.update(kwargs)
    with pytest.raises(ValueError):
        learning.create_job(**values)



def test_new_topic_starts_with_ai_learning_and_persists_method_ownership(method_job_store, monkeypatch):
    topic_id = method_job_store
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})

    result = learning_method_orchestrator.queue_initial_method(topic_id)

    assert result["status"] == "queued"
    assert result["method"] == "ai_learning"
    assert result["job"]["learning_method"] == "ai_learning"
    assert result["method_attempt_key"] == f"path-topic:{topic_id}:method:ai_learning:attempt:1"
    events = list_attempt_events(topic_id)
    assert events[0]["status"] == "queued"
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT current_job_id, state FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
    assert gate["current_job_id"] == result["job"]["id"]
    assert gate["state"] == "pending"


def test_generation_failure_advances_to_basic_once_and_is_replay_safe(method_job_store, monkeypatch):
    topic_id = method_job_store
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})
    initial = learning_method_orchestrator.queue_initial_method(topic_id)
    job_id = int(initial["job"]["id"])
    learning_method_orchestrator.mark_method_running(job_id)
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='failed', error='provider timeout' WHERE id=?",
            (job_id,),
        )

    task_context = _learning_task_context(job_id)
    first = learning_method_orchestrator.record_generation_failure(
        job_id, reason="provider timeout", task_context=task_context
    )
    replay = learning_method_orchestrator.record_generation_failure(
        job_id, reason="provider timeout", task_context=task_context
    )

    assert first["method"] == "basic_learning"
    assert replay["job"]["id"] == first["job"]["id"]
    events = list_attempt_events(topic_id)
    assert [event["status"] for event in events] == ["queued", "running", "failed", "queued"]
    with db.learning_connection() as connection:
        jobs = connection.execute(
            "SELECT learning_method, COUNT(*) AS count FROM learning_jobs WHERE path_topic_id=? GROUP BY learning_method",
            (topic_id,),
        ).fetchall()
    assert {row["learning_method"]: row["count"] for row in jobs} == {
        "ai_learning": 1, "basic_learning": 1,
    }


def test_three_method_failures_block_topic_without_fourth_method(method_job_store, monkeypatch):
    topic_id = method_job_store
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})
    transition = learning_method_orchestrator.queue_initial_method(topic_id)

    for expected_method in ("ai_learning", "basic_learning", "topic_learning"):
        assert transition["method"] == expected_method
        job_id = int(transition["job"]["id"])
        learning_method_orchestrator.mark_method_running(job_id)
        with db.learning_connection() as connection:
            connection.execute(
                "UPDATE learning_jobs SET status='failed', error=? WHERE id=?",
                (expected_method + " failed", job_id),
            )
        task_context = _learning_task_context(job_id)
        transition = learning_method_orchestrator.record_generation_failure(
            job_id, reason=expected_method + " failed", task_context=task_context
        )

    assert transition["status"] == "blocked"
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        topic = connection.execute(
            "SELECT status FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
        jobs = connection.execute(
            "SELECT learning_method FROM learning_jobs WHERE path_topic_id=? ORDER BY id",
            (topic_id,),
        ).fetchall()
    assert gate["state"] == "blocked"
    assert topic["status"] == "needs_review"
    assert [row["learning_method"] for row in jobs] == [
        "ai_learning", "basic_learning", "topic_learning",
    ]


def test_permanent_method_job_creation_failure_advances_to_next_method(method_job_store, monkeypatch):
    topic_id = method_job_store
    original_create = learning.create_job
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})

    def fail_ai_job(*args, **kwargs):
        if kwargs.get("learning_method") == "ai_learning":
            raise ValueError("invalid AI method job contract")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(learning, "create_job", fail_ai_job)
    result = learning_method_orchestrator.queue_initial_method(topic_id)

    assert result["method"] == "basic_learning"
    events = list_attempt_events(topic_id)
    assert [(event["method"], event["status"]) for event in events] == [
        ("ai_learning", "queued"),
        ("ai_learning", "failed"),
        ("basic_learning", "queued"),
    ]
    assert events[1]["diagnosis_json"]
    assert events[1]["evidence_json"]


def test_user_disabled_ai_learning_falls_back_to_basic_learning(method_job_store, monkeypatch):
    topic_id = method_job_store
    db.update_settings({"ai_learning_enabled": False})
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})

    result = learning_method_orchestrator.queue_initial_method(topic_id)

    assert result["method"] == "basic_learning"
    methods = list_attempt_events(topic_id)
    assert [(row["method"], row["status"]) for row in methods] == [
        ("ai_learning", "unavailable"),
        ("basic_learning", "queued"),
    ]


def test_user_cancellation_does_not_trigger_method_fallback(method_job_store, monkeypatch):
    topic_id = method_job_store
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})
    initial = learning_method_orchestrator.queue_initial_method(topic_id)
    job_id = int(initial["job"]["id"])
    learning_method_orchestrator.mark_method_running(job_id)
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='cancelled', error='user cancelled' WHERE id=?",
            (job_id,),
        )
    learning_method_orchestrator.mark_method_cancelled(job_id)
    result = learning_method_orchestrator.queue_initial_method(topic_id)
    assert result["status"] == "cancelled"
    assert list_attempt_events(topic_id)[-1]["status"] == "cancelled"
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()[0]
    assert count == 1
