import hashlib
import json

import pytest

from app import db, learning


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("candidate topic", source_text="Source text is evidence, not proof.")
    lesson = "A candidate lesson that must pass its declared tests before promotion."
    digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    with db.learning_connection() as connection:
        candidate_id = int(connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   source_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["topic"], lesson, "user_provided_text",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid)
    return job, candidate_id, lesson


def test_candidate_is_promoted_only_after_all_declared_tests_pass(tmp_path, monkeypatch):
    job, candidate_id, lesson = _setup(tmp_path, monkeypatch)
    plan = [
        {"test": "output_matches_reference", "purpose": "Check the defined expected output."},
        {"test": "edge_case", "purpose": "Check a boundary input."},
    ]
    results = [
        {"test": "output_matches_reference", "runner": "deterministic_assertion",
         "passed": True, "expected": "42", "actual": "42"},
        {"test": "edge_case", "runner": "sandbox",
         "passed": True, "expected": "[]", "actual": "[]"},
    ]

    candidate = learning.record_candidate_test_results(candidate_id, plan, results)

    assert candidate["status"] == "passed"
    assert candidate["promoted_knowledge_id"] is not None
    with db.learning_connection() as connection:
        knowledge = connection.execute(
            "SELECT content, status, job_id FROM knowledge_items WHERE id=?",
            (candidate["promoted_knowledge_id"],),
        ).fetchone()
        experiences = connection.execute("SELECT COUNT(*) FROM learning_experiences").fetchone()[0]
    assert tuple(knowledge) == (lesson, "verified", job["id"])
    assert experiences == 0
    listed = learning.list_temporary_knowledge_candidates("passed")
    assert listed[0]["id"] == candidate_id
    assert listed[0]["test_results"] == results


def test_failed_candidate_stays_temporary_and_creates_failure_experience(tmp_path, monkeypatch):
    _job, candidate_id, _lesson = _setup(tmp_path, monkeypatch)
    plan = [{"test": "expected_result", "purpose": "Compare against a fixed reference."}]
    results = [{
        "test": "expected_result", "runner": "reference_dataset",
        "passed": False, "expected": "42", "actual": "41",
    }]

    candidate = learning.record_candidate_test_results(candidate_id, plan, results)

    assert candidate["status"] == "failed"
    assert candidate["promoted_knowledge_id"] is None
    with db.learning_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0
        experience = connection.execute(
            """SELECT topic, problem, status, test_plan_json, test_results_json, takeaway
               FROM learning_experiences"""
        ).fetchone()
    assert experience["status"] == "failed"
    assert "expected_result" in experience["problem"]
    assert json.loads(experience["test_results_json"]) == results
    assert "expected" in experience["takeaway"] or "آزمون" in experience["takeaway"]


def test_candidate_test_results_must_match_plan_and_name_automated_runner(tmp_path, monkeypatch):
    _job, candidate_id, _lesson = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="match the planned test"):
        learning.record_candidate_test_results(
            candidate_id,
            [{"test": "fixed_reference"}],
            [{"test": "different_test", "runner": "sandbox", "passed": True,
              "expected": "x", "actual": "x"}],
        )
    with pytest.raises(ValueError, match="approved automated runner"):
        learning.record_candidate_test_results(
            candidate_id,
            [{"test": "fixed_reference"}],
            [{"test": "fixed_reference", "runner": "model_opinion", "passed": True,
              "expected": "x", "actual": "x"}],
        )
    with db.learning_connection() as connection:
        status = connection.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"
