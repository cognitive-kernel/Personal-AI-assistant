import json

import pytest

from app import db, learning, learning_practical


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _completed_python_job():
    job = learning.create_job("Python JSON processing")
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', lesson=? WHERE id=?",
            ("A completed Python lesson with JSON input/output examples.", job["id"]),
        )
    return job["id"]


def _valid_report(passed=4):
    cases = learning_practical._TEST_CASES[learning_practical.EXERCISE_ID]
    results = [{"case_id": item["id"], "passed": index < passed} for index, item in enumerate(cases)]
    return {
        "schema_version": 1,
        "exercise_id": learning_practical.EXERCISE_ID,
        "grading_version": learning_practical.EXERCISES[learning_practical.EXERCISE_ID]["grading_version"],
        "total": len(cases),
        "passed": passed,
        "results": results,
    }


def test_practical_catalog_hides_grading_cases():
    exercises = learning_practical.list_exercises()

    assert len(exercises) == 1
    assert exercises[0]["id"] == learning_practical.EXERCISE_ID
    assert "topic_keywords" not in exercises[0]
    assert "expected" not in json.dumps(exercises)


def test_practical_grader_uses_server_side_versioned_cases_and_json_outputs():
    cases = learning_practical.get_test_cases(learning_practical.EXERCISE_ID)

    assert len(cases) == 4
    assert cases[0]["id"] == "positive-integers"
    assert cases[0]["expected"] == 6
    assert learning_practical.grade_case_output(
        learning_practical.EXERCISE_ID, cases[0]["id"], "succeeded", 0, "6\\n"
    )
    assert not learning_practical.grade_case_output(
        learning_practical.EXERCISE_ID, cases[0]["id"], "succeeded", 0, "5\\n"
    )
    assert not learning_practical.grade_case_output(
        learning_practical.EXERCISE_ID, cases[0]["id"], "succeeded", 0, "not JSON"
    )


def test_practical_attempt_is_persisted_but_not_promoted_without_course_path(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    job_id = _completed_python_job()
    source = "import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps(sum(data['values'])))"

    result = learning_practical.record_practical_attempt(
        job_id, learning_practical.EXERCISE_ID, _valid_report(), source
    )

    assert result["score"] == 100.0
    assert result["passed_tests"] == 4
    assert result["scoring_method"] == "sandbox_practical_v1"
    assert result["mastery_achieved"] is False
    assert result["independent_execution"] is True
    with db.learning_connection() as conn:
        attempt = conn.execute(
            "SELECT score, scoring_method, answers_json FROM assessment_attempts WHERE id=?",
            (result["attempt_id"],),
        ).fetchone()
    assert attempt["score"] == 100
    assert attempt["scoring_method"] == "sandbox_practical_v1"
    assert json.loads(attempt["answers_json"])["exercise_id"] == learning_practical.EXERCISE_ID


def test_practical_attempt_rejects_inconsistent_grading_report(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    job_id = _completed_python_job()
    report = _valid_report()
    report["passed"] = 3

    with pytest.raises(ValueError, match="failed validation"):
        learning_practical.record_practical_attempt(
            job_id, learning_practical.EXERCISE_ID, report, "print(0)"
        )


def test_practical_exercise_requires_completed_relevant_lesson(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    job = learning.create_job("Python JSON processing")

    with pytest.raises(ValueError, match="completed lesson"):
        learning_practical.validate_practical_job(job["id"], learning_practical.EXERCISE_ID)

    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='completed' WHERE id=?", (job["id"],))
        conn.execute("UPDATE learning_jobs SET topic='History' WHERE id=?", (job["id"],))
    with pytest.raises(ValueError, match="does not match"):
        learning_practical.validate_practical_job(job["id"], learning_practical.EXERCISE_ID)


def test_practical_sandbox_task_grades_cases_outside_learner_process(monkeypatch, tmp_path):
    from app import sandbox_tasks
    from app.sandbox import SandboxResult

    executable = tmp_path / "docker"
    executable.write_text("fake executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(executable))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)
    calls = []
    report = _valid_report()

    def fake_run(source, **kwargs):
        calls.append((source, kwargs.get("stdin_text")))
        data = json.loads(kwargs["stdin_text"])
        return SandboxResult("succeeded", 0, json.dumps(sum(data["values"])), "", 25)

    monkeypatch.setattr(sandbox_tasks.sandbox, "run_python", fake_run)
    monkeypatch.setattr(
        learning_practical, "record_practical_attempt",
        lambda job_id, exercise_id, grading_report, source: {
            "job_id": job_id, "exercise_id": exercise_id, "score": 100.0,
            "received_report": grading_report == report, "source_sha256": "hash",
        },
    )

    class Context:
        task_id = 9
        def checkpoint(self, _value):
            return {"status": "running"}
        def control_status(self):
            return "running"

    handler = sandbox_tasks.handler_from_environment()
    source = "import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps(sum(data['values'])))"
    result = handler(
        {
            "source_code": source,
            "learning_practical": {"exercise_id": learning_practical.EXERCISE_ID, "job_id": 7},
        },
        None,
        Context(),
    )

    assert result["execution_status"] == "succeeded"
    assert result["practical_assessment"]["score"] == 100.0
    assert result["practical_assessment"]["received_report"] is True
    assert len(calls) == 4
    assert all(code == source for code, _input in calls)
    assert all(isinstance(json.loads(stdin)["values"], list) for _source, stdin in calls)


def test_practical_mastery_completes_python_path_topic_automatically(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    path = learning.enroll_curriculum("Python")
    first_topic = path["topics"][0]
    job = learning.create_job(first_topic["topic"], path_topic_id=first_topic["id"])
    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='completed' WHERE id=?", (job["id"],))
    source = "import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps(sum(data['values'])))"

    result = learning_practical.record_practical_attempt(
        job["id"], learning_practical.EXERCISE_ID, _valid_report(), source
    )
    refreshed = learning.get_learning_path(path["id"])

    assert result["mastery_achieved"] is True
    assert refreshed["topics"][0]["status"] == "completed"
    assert refreshed["topics"][0]["latest_score"] == 100
