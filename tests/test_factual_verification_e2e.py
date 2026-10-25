import hashlib
import json
import re

from app import db, factual_knowledge, factual_verification_routes, factual_verification_tasks, sandbox_consent
from app.task_worker import TaskWorker


def _valid_semantic_assessment(knowledge_id, source_code):
    candidate = factual_verification_tasks.get_verification_candidate(knowledge_id)
    return {
        "claim_hash": candidate["claim_hash"],
        "test_source_hash": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        "verdict": "supports",
        "claim_atomic": True,
        "source_directly_addresses_claim": True,
        "scope_matches": True,
        "source_version_matches_claim": True,
        "runtime_environment_matches": True,
        "source_is_authoritative": True,
        "no_unresolved_conflict": True,
        "reviewer_is_independent": True,
    }


def _create_active_sourced_claim(version="3.12.3"):
    with db.learning_connection() as connection:
        claim_id = factual_knowledge.create_claim(
            connection,
            "A Python assertion raises AssertionError when its condition is false.",
            environment_name="python",
            environment_version=version,
            scope={"topic": "Python assert"},
        )
        factual_knowledge.add_evidence(
            connection,
            claim_id,
            source_type="official_docs",
            source_ref="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
            source_hash="sha256:python-assert-reference",
            excerpt="The assert statement raises AssertionError when its expression is false.",
        )
        factual_knowledge.transition_evidence_level(connection, claim_id, "L1_sourced")
        factual_knowledge.transition_lifecycle(connection, claim_id, "active")
    return claim_id


def test_factual_claim_route_queue_worker_and_result_are_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    claim_id = _create_active_sourced_claim()

    monkeypatch.setattr(
        factual_verification_routes.sandbox_consent,
        "get_consent",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(factual_verification_routes, "_configured", lambda: True)
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)

    test_source_code = "try:\n    assert False\nexcept AssertionError:\n    pass\nelse:\n    raise RuntimeError('assert did not fail')"
    request = factual_verification_routes.FactualClaimTestRequest(
        confirm=True,
        oracle_reviewed=True,
        oracle_review_note="Compared this exact test with the official Python assert reference.",
        oracle_source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
        oracle_source_hash=hashlib.sha256(b"Official Python assert reference excerpt").hexdigest(),
        test_name="Python assert raises on false condition",
        source_code=test_source_code,
        semantic_assessment=_valid_semantic_assessment(claim_id, test_source_code),
        timeout_seconds=5,
        max_output_bytes=2048,
    )
    queued = factual_verification_routes.queue_factual_claim_test(claim_id, request)
    task_id = queued["task"]["id"]

    def fake_sandbox(payload, _checkpoint, _context):
        wrapper = payload["source_code"]
        marker_match = re.search(r"__PAI_TEST_COMPLETED__:[0-9a-f]{32}", wrapper)
        assert marker_match is not None
        marker = marker_match.group(0)
        return {
            "phase": "sandbox_finished",
            "source_sha256": hashlib.sha256(wrapper.encode("utf-8")).hexdigest(),
            "image": "python:3.12-slim@sha256:" + "a" * 64,
            "execution_status": "succeeded",
            "exit_code": 0,
            "duration_ms": 12,
            "stdout": "__PAI_PYTHON_VERSION__:3.12.3\nassertion check passed\n" + marker + "\n",
            "stderr": "",
            "reason": None,
        }

    handler = factual_verification_tasks._build_handler(fake_sandbox)
    worker = TaskWorker(
        "factual-verification-e2e",
        {factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE: handler},
        restrict_task_types=True,
    )
    assert worker.run_once() is True

    with db.learning_connection() as connection:
        task = connection.execute(
            "SELECT status, result_json FROM task_queue WHERE id=?",
            (task_id,),
        ).fetchone()
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (claim_id,),
        ).fetchone()
        tests = connection.execute(
            "SELECT outcome, environment_version, command_hash, log_hash FROM factual_tests WHERE knowledge_id=?",
            (claim_id,),
        ).fetchall()

    assert task["status"] == "succeeded"
    result = json.loads(task["result_json"])
    assert result["outcome"] == "passed"
    assert result["evidence_level_promoted"] is True
    assert tuple(claim) == ("L2_tested", "active")
    assert len(tests) == 1
    assert tests[0]["outcome"] == "passed"
    assert tests[0]["environment_version"] == "3.12.3"
    assert len(tests[0]["command_hash"]) == 64
    assert len(tests[0]["log_hash"]) == 64
