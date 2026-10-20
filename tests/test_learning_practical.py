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


def test_grader_wrapper_runs_learner_as_a_child_program():
    source = "import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps(sum(data['values'])))"

    wrapper = learning_practical.build_grader_source(learning_practical.EXERCISE_ID, source)

    assert "subprocess.run(" in wrapper
    assert "timeout=2" in wrapper
    assert "positive-integers" in wrapper
    assert source in wrapper


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
