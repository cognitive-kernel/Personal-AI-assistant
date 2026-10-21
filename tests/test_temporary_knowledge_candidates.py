import hashlib
import json

import pytest

from app import db, learning
from app.knowledge_review import record_peer_consultation
from app.learning_consensus import evaluate_candidate_consensus


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




def _establish_l1_consensus(candidate_id):
    from app.knowledge_review import record_peer_consultation
    from app.learning_consensus import evaluate_candidate_consensus
    for provider, model in (("peer-fixture-a", "reviewer-model-a"), ("peer-fixture-b", "reviewer-model-b")):
        record_peer_consultation(
            candidate_id, provider=provider, model_name=model,
            prompt="Review the exact candidate against retained source context.",
            response="Supports the bounded claim for the stated source scope.",
            stance="supports",
        )
    result = evaluate_candidate_consensus(candidate_id)
    assert result["trust_level"] == "L1"
    assert result["outcome"] == "model_consensus"

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
    # The lesson fixture extracts one claim; map both tests to that claim explicitly.
    for planned_test in plan:
        planned_test["claim_ids"] = [f"{candidate_id}:1"]
    results = [case1[1], case2[1]]
    _establish_l1_consensus(candidate_id)

    candidate = learning.record_candidate_test_results(candidate_id, plan, results)

    assert candidate["status"] == "passed"
    assert candidate["promoted_knowledge_id"] is not None
    with db.learning_connection() as connection:
        knowledge = connection.execute(
            "SELECT content, status, trust_level, learning_status, job_id FROM knowledge_items WHERE id=?",
            (candidate["promoted_knowledge_id"],),
        ).fetchone()
        experiences = connection.execute("SELECT COUNT(*) FROM learning_experiences").fetchone()[0]
    assert tuple(knowledge) == (lesson, "verified", "L2", "tested", job["id"])
    assert experiences == 0
    listed = learning.list_temporary_knowledge_candidates("passed")
    assert listed[0]["id"] == candidate_id
    assert listed[0]["test_results"] == results


def test_promotion_upgrades_existing_unverified_knowledge_row_in_place(tmp_path, monkeypatch):
    job, candidate_id, lesson = _setup(tmp_path, monkeypatch)
    with db.learning_connection() as connection:
        connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, 1, 'unverified', ?)""",
            (job["topic"], lesson, "user_provided_text", job["id"]),
        )
        original_id = connection.execute(
            "SELECT id FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchone()["id"]
    case = _case("existing_row_upgrade", "ok", "ok", "deterministic_assertion")
    case[0]["claim_ids"] = [f"{candidate_id}:1"]
    _establish_l1_consensus(candidate_id)
    candidate = learning.record_candidate_test_results(candidate_id, [case[0]], [case[1]])
    with db.learning_connection() as connection:
        rows = connection.execute(
            "SELECT id, status, content FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == original_id == candidate["promoted_knowledge_id"]
    assert rows[0]["status"] == "verified"
    assert rows[0]["content"] == lesson
    with db.learning_connection() as connection:
        metadata = connection.execute(
            "SELECT trust_level, learning_status FROM knowledge_items WHERE id=?", (original_id,)
        ).fetchone()
    assert tuple(metadata) == ("L2", "tested")


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
    forged_expected = dict(valid_plan, expected="attacker-controlled")
    forged_expected["oracle_sha256"] = hashlib.sha256(
        json.dumps("attacker-controlled", ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    forged_output = dict(
        valid_result, expected="attacker-controlled", actual="attacker-controlled",
        oracle_sha256=forged_expected["oracle_sha256"],
    )
    with pytest.raises(ValueError, match="exactly match the trusted fixture"):
        learning.record_candidate_test_results(candidate_id, [forged_expected], [forged_output])

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


def test_passing_result_cannot_disagree_with_trusted_expected_output(tmp_path, monkeypatch):
    _job, candidate_id, _lesson = _setup(tmp_path, monkeypatch)
    plan, result = _case("oracle_equality", 42, 42, "deterministic_assertion")
    forged_result = dict(result, actual=41, passed=True)

    with pytest.raises(ValueError, match="must exactly match the trusted oracle"):
        learning.record_candidate_test_results(candidate_id, [plan], [forged_result])

    with db.learning_connection() as connection:
        status = connection.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0
    assert status == "awaiting_test"


def test_source_example_oracle_must_be_allowlisted_and_regression_fixture_cannot_escape(tmp_path, monkeypatch):
    _job, candidate_id, _lesson = _setup(tmp_path, monkeypatch)
    digest = hashlib.sha256(b"expected").hexdigest()
    untrusted_source_plan = [{
        "test": "untrusted_web_example",
        "oracle_kind": "source_example",
        "oracle_ref": "https://example.org/tutorial",
        "expected": "expected",
        "oracle_sha256": digest,
    }]
    result = [{
        "test": "untrusted_web_example", "runner": "sandbox", "passed": True,
        "expected": "expected", "actual": "expected", "oracle_sha256": digest,
    }]
    with pytest.raises(ValueError, match="allowlisted primary source"):
        learning.record_candidate_test_results(candidate_id, untrusted_source_plan, result)

    fixture_plan, fixture_result = _case("path_traversal", "expected", "expected", "sandbox")
    fixture_plan["oracle_ref"] = "tests/fixtures/../../outside.json"
    with pytest.raises(ValueError, match="inside tests/fixtures"):
        learning.record_candidate_test_results(candidate_id, [fixture_plan], [fixture_result])
