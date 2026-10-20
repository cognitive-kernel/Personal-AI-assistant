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
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["topic"], lesson, "user_provided_text",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid)
    return job, candidate_id, lesson



def _case(name, expected, actual, runner, *, passed=True):
    digest = hashlib.sha256(
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        {"test": name, "purpose": "Fixed, versioned regression fixture.",
         "oracle_kind": "regression_fixture", "oracle_ref": "tests/fixtures/learning/reference-v1.json",
         "expected": expected, "oracle_sha256": digest},
        {"test": name, "runner": runner, "passed": passed,
         "expected": expected, "actual": actual, "oracle_sha256": digest},
    )

def test_candidate_is_promoted_only_after_all_declared_tests_pass(tmp_path, monkeypatch):
    job, candidate_id, lesson = _setup(tmp_path, monkeypatch)
    case1 = _case("output_matches_reference", "42", "42", "deterministic_assertion")
    case2 = _case("edge_case", [], [], "sandbox")
    plan = [case1[0], case2[0]]
    results = [case1[1], case2[1]]

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
    case = _case("expected_result", "42", "41", "reference_dataset", passed=False)
    plan = [case[0]]
    results = [case[1]]

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
    valid_plan, valid_result = _case("fixed_reference", "x", "x", "sandbox")
    mismatched = dict(valid_result, test="different_test")
    with pytest.raises(ValueError, match="match the planned test"):
        learning.record_candidate_test_results(candidate_id, [valid_plan], [mismatched])
    invalid_runner = dict(valid_result, runner="model_opinion")
    with pytest.raises(ValueError, match="approved automated runner"):
        learning.record_candidate_test_results(candidate_id, [valid_plan], [invalid_runner])
    untrusted_plan = {"test": "model_opinion_only", "purpose": "AI says so", "expected": "x"}
    with pytest.raises(ValueError, match="trusted oracle kind"):
        learning.record_candidate_test_results(candidate_id, [untrusted_plan], [valid_result])
    bad_digest_plan = dict(valid_plan, oracle_sha256="0" * 64)
    with pytest.raises(ValueError, match="Oracle digest"):
        learning.record_candidate_test_results(candidate_id, [bad_digest_plan], [valid_result])
    with db.learning_connection() as connection:
        status = connection.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"
