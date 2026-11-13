import json

import pytest

from app import db, learning, task_manager
from app.learning_topic_quality_tasks import (
    TOPIC_QUALITY_TASK_TYPE,
    enqueue_topic_quality_evaluation,
    handle_topic_quality_evaluation,
)


class RecordingContext:
    def __init__(self):
        self.checkpoints = []

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}


@pytest.fixture
def topic_quality_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def _completed_topic_job():
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('quality-gate-test', 'Quality gate test')"
        ).lastrowid)
        topic_id = int(connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position, status) VALUES (?, 'Python functions', 1, 'in_progress')",
            (path_id,),
        ).lastrowid)
        connection.execute(
            """INSERT INTO learning_topic_quality_gates(
                   path_topic_id, state, max_repairs, next_action
               ) VALUES (?, 'pending', 3, 'await_first_lesson')""",
            (topic_id,),
        )
    job = learning.create_job("Python functions", path_topic_id=topic_id)
    lesson = (
        "Python functions define reusable behavior. The def statement creates a function. "
        "For example, a function can validate a value because it isolates one responsibility. "
        "Parameters, return values, exceptions, and scope determine how a function behaves."
    )
    with db.learning_connection() as connection:
        connection.execute(
            """UPDATE learning_jobs
               SET status='completed', lesson=?, assessment_json=?, quality_json=?
               WHERE id=?""",
            (
                lesson,
                json.dumps({
                    "learning_outcomes": ["Define a function", "Use parameters", "Explain return values"],
                    "questions": ["What does def do?", "How are parameters passed?", "When is a return value used?"],
                    "answer_key": ["It defines a function.", "Parameters receive input values.", "Return sends a result to the caller."],
                }),
                json.dumps({"score": 90, "reasons": []}),
                job["id"],
            ),
        )
        connection.execute(
            "UPDATE learning_topic_quality_gates SET current_job_id=?, state='evaluating' WHERE path_topic_id=?",
            (job["id"], topic_id),
        )
    return path_id, topic_id, int(job["id"]), lesson


def _evaluation(*, passed, score=90):
    dimensions = {
        "technical_correctness": {"score": score, "rationale": "The concepts are technically sound.", "failed_checks": []},
        "topic_coverage": {"score": score, "rationale": "The stated learning outcomes are covered.", "failed_checks": []},
        "version_applicability": {"score": score, "rationale": "No unsupported version claim was found.", "failed_checks": []},
        "source_support": {"score": score, "rationale": "The evidence status is represented accurately.", "failed_checks": []},
        "examples_and_validation": {"score": score, "rationale": "The example is clear and reproducible.", "failed_checks": []},
    }
    critical = []
    if not passed:
        dimensions["technical_correctness"]["failed_checks"] = ["A required behavior is inaccurate."]
        critical = [{
            "code": "incorrect_behavior",
            "evidence": "The return-value explanation contradicts the supplied reference.",
            "root_cause": "The lesson conflates printing with returning a value.",
            "correction": "Rewrite the explanation and add a caller-side example that inspects the returned value.",
        }]
    return {
        "version": "topic-content-review-v1",
        "job_id": 1,
        "path_topic_id": 1,
        "content_sha256": "a" * 64,
        "evaluator_digest": "b" * 64,
        "evaluated_at": "2026-11-13T00:00:00Z",
        "score": float(score),
        "pass_threshold": 80,
        "passed": passed,
        "evaluator_verdict": "pass" if passed else "fail",
        "confidence": "high",
        "dimensions": dimensions,
        "critical_failures": critical,
        "overall_diagnosis": "Lesson review diagnosis.",
        "repair_instructions": [] if passed else ["Correct the return-value explanation."],
    }


def test_topic_evaluation_passes_before_advancing_and_is_immutable(
    topic_quality_store, monkeypatch
):
    _path_id, topic_id, job_id, lesson = _completed_topic_job()
    import app.learning_topic_evaluator as evaluator
    monkeypatch.setattr(evaluator, "evaluate_lesson", lambda _job_id: {
        **_evaluation(passed=True),
        "job_id": job_id,
        "path_topic_id": topic_id,
        "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
    })
    advanced = []
    monkeypatch.setattr(learning, "queue_next_researched_path_topic", lambda value: advanced.append(value) or {"status": "queued"})

    result = handle_topic_quality_evaluation(
        {
            "job_id": job_id,
            "path_topic_id": topic_id,
            "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
        },
        None,
        RecordingContext(),
    )

    assert result["status"] == "passed"
    assert result["score"] == 90
    assert advanced == [topic_id]
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state, latest_score FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_topic_quality_evaluations WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()[0]
        with pytest.raises(db.sqlite3.IntegrityError):
            connection.execute(
                "UPDATE learning_topic_quality_evaluations SET score=0 WHERE path_topic_id=?",
                (topic_id,),
            )
    assert gate["state"] == "passed"
    assert gate["latest_score"] == 90
    assert count == 1


def test_failed_topic_is_diagnosed_and_repaired_without_advancing(
    topic_quality_store, monkeypatch
):
    _path_id, topic_id, job_id, lesson = _completed_topic_job()
    import app.learning_topic_evaluator as evaluator
    monkeypatch.setattr(evaluator, "evaluate_lesson", lambda _job_id: {
        **_evaluation(passed=False, score=60),
        "job_id": job_id,
        "path_topic_id": topic_id,
        "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
    })
    enqueued = []
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda value: enqueued.append(value) or {"id": value})
    advanced = []
    monkeypatch.setattr(learning, "queue_next_researched_path_topic", lambda value: advanced.append(value))

    payload = {
        "job_id": job_id,
        "path_topic_id": topic_id,
        "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
    }
    result = handle_topic_quality_evaluation(payload, None, RecordingContext())

    assert result["status"] == "repairing"
    assert result["score"] == 60
    assert result["repair_count"] == 1
    assert advanced == []
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state, repair_count, current_job_id, diagnosis_json FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        jobs = connection.execute(
            "SELECT id, lesson_review_json, correction_request_key FROM learning_jobs WHERE path_topic_id=? ORDER BY id",
            (topic_id,),
        ).fetchall()
    assert gate["state"] == "repairing"
    assert gate["repair_count"] == 1
    assert int(gate["current_job_id"]) == int(jobs[-1]["id"])
    assert len(jobs) == 2
    correction_context = json.loads(jobs[-1]["lesson_review_json"])
    assert correction_context["correction_mode"] == "automated_topic_quality_gate"
    assert correction_context["prior_job_id"] == job_id
    assert jobs[-1]["correction_request_key"]
    assert enqueued == [int(jobs[-1]["id"])]
    assert json.loads(gate["diagnosis_json"])["critical_failures"]


def test_topic_evaluation_enqueue_is_idempotent(topic_quality_store):
    _path_id, topic_id, job_id, lesson = _completed_topic_job()
    first = enqueue_topic_quality_evaluation(job_id)
    second = enqueue_topic_quality_evaluation(job_id)

    assert first["status"] == "queued"
    assert second["status"] == "queued"
    assert first["task_id"] == second["task_id"]
    tasks = [
        task for task in task_manager.list_tasks()
        if task["task_type"] == TOPIC_QUALITY_TASK_TYPE
        and task["payload"]["path_topic_id"] == topic_id
    ]
    assert len(tasks) == 1
    assert tasks[0]["payload"]["content_sha256"] == __import__("hashlib").sha256(lesson.encode()).hexdigest()



def _method_scoped_completed_job(monkeypatch):
    from app import learning_method_orchestrator

    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('method-quality-test', 'Method quality test')"
        ).lastrowid)
        topic_id = int(connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position, status) VALUES (?, 'Independent methods', 1, 'in_progress')",
            (path_id,),
        ).lastrowid)
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": f"task-{job_id}"})
    transition = learning_method_orchestrator.queue_initial_method(topic_id)
    job_id = int(transition["job"]["id"])
    learning_method_orchestrator.mark_method_running(job_id)
    lesson = (
        "A complete lesson explains the concept, its prerequisites, a worked example, "
        "expected output, common errors, and a short practice task with a verified answer."
    )
    with db.learning_connection() as connection:
        connection.execute(
            """UPDATE learning_jobs SET status='completed', lesson=?, assessment_json=?, quality_json=?
               WHERE id=?""",
            (
                lesson,
                json.dumps({"learning_outcomes": ["Understand the concept"], "questions": ["Explain it"], "answer_key": ["A clear explanation."]}),
                json.dumps({"score": 90, "reasons": []}),
                job_id,
            ),
        )
        connection.execute(
            "UPDATE learning_topic_quality_gates SET state='evaluating', current_job_id=? WHERE path_topic_id=?",
            (job_id, topic_id),
        )
    return path_id, topic_id, job_id, lesson


def test_method_scoped_quality_failure_queues_basic_learning_not_same_lane(
    topic_quality_store, monkeypatch
):
    _path_id, topic_id, job_id, lesson = _method_scoped_completed_job(monkeypatch)
    import app.learning_topic_evaluator as evaluator
    monkeypatch.setattr(evaluator, "evaluate_lesson", lambda _job_id: {
        **_evaluation(passed=False, score=60),
        "job_id": job_id,
        "path_topic_id": topic_id,
        "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
    })
    monkeypatch.setattr(
        learning, "submit_lesson_issue",
        lambda *_args, **_kwargs: pytest.fail("same-method repair must not be used for method-scoped topics"),
    )

    result = handle_topic_quality_evaluation(
        {
            "job_id": job_id,
            "path_topic_id": topic_id,
            "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
        },
        None,
        RecordingContext(),
    )

    assert result["status"] == "queued"
    assert result["method_transition"]["method"] == "basic_learning"
    with db.learning_connection() as connection:
        jobs = connection.execute(
            "SELECT learning_method FROM learning_jobs WHERE path_topic_id=? ORDER BY id",
            (topic_id,),
        ).fetchall()
    assert [row["learning_method"] for row in jobs] == ["ai_learning", "basic_learning"]


def test_method_scoped_quality_pass_closes_gate_and_advances_once(
    topic_quality_store, monkeypatch
):
    _path_id, topic_id, job_id, lesson = _method_scoped_completed_job(monkeypatch)
    import app.learning_topic_evaluator as evaluator
    monkeypatch.setattr(evaluator, "evaluate_lesson", lambda _job_id: {
        **_evaluation(passed=True),
        "job_id": job_id,
        "path_topic_id": topic_id,
        "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
    })
    advanced = []
    monkeypatch.setattr(learning, "queue_next_researched_path_topic", lambda value: advanced.append(value) or {"status": "completed"})

    result = handle_topic_quality_evaluation(
        {
            "job_id": job_id,
            "path_topic_id": topic_id,
            "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
        },
        None,
        RecordingContext(),
    )

    assert result["status"] == "passed"
    assert result["method_transition"]["method"] == "ai_learning"
    assert advanced == [topic_id]
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state, current_job_id FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
    assert gate["state"] == "passed"
    assert gate["current_job_id"] == job_id


def test_method_scoped_evaluator_unavailable_blocks_without_skipping_to_basic(
    topic_quality_store, monkeypatch
):
    _path_id, topic_id, job_id, lesson = _method_scoped_completed_job(monkeypatch)
    import app.learning_topic_evaluator as evaluator
    monkeypatch.setattr(evaluator, "evaluate_lesson", lambda _job_id: (_ for _ in ()).throw(RuntimeError("evaluator offline")))

    result = handle_topic_quality_evaluation(
        {
            "job_id": job_id,
            "path_topic_id": topic_id,
            "content_sha256": __import__("hashlib").sha256(lesson.encode()).hexdigest(),
        },
        None,
        RecordingContext(),
    )

    assert result["status"] == "blocked"
    with db.learning_connection() as connection:
        gate = connection.execute(
            "SELECT state FROM learning_topic_quality_gates WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE path_topic_id=?",
            (topic_id,),
        ).fetchone()[0]
    assert gate["state"] == "blocked"
    assert count == 1
