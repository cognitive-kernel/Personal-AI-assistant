import hashlib
import json
import re

import pytest

from app import db, factual_knowledge, factual_verification_routes, factual_verification_tasks, sandbox_consent, task_manager


class FakeContext:
    def __init__(self):
        self.task_id = 712
        self.status = "running"
        self.checkpoints = []

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}

    def control_status(self):
        return self.status


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return tmp_path


def _active_sourced_python_claim(version="3.12.3"):
    with db.learning_connection() as connection:
        knowledge_id = factual_knowledge.create_claim(
            connection,
            "A Python assertion raises an error when its condition is false.",
            environment_name="python",
            environment_version=version,
            scope={"topic": "Python assertions"},
        )
        factual_knowledge.add_evidence(
            connection,
            knowledge_id,
            source_type="official_docs",
            source_ref="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
            source_hash="sha256:source-evidence",
            excerpt="The assert statement raises AssertionError when the expression is false.",
        )
        factual_knowledge.transition_evidence_level(connection, knowledge_id, "L1_sourced")
        factual_knowledge.transition_lifecycle(connection, knowledge_id, "active")
    return knowledge_id


def _oracle_review_id(knowledge_id, source_code):
    candidate = factual_verification_tasks.get_verification_candidate(knowledge_id)
    with db.learning_connection() as connection:
        return factual_knowledge.record_oracle_review(
            connection,
            knowledge_id,
            claim_hash=candidate["claim_hash"],
            test_source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            oracle_source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
            oracle_source_hash=hashlib.sha256(
                b"Python assert statement reference excerpt reviewed independently"
            ).hexdigest(),
            review_note="Compared the test assertion against the official Python reference excerpt.",
        )


def _sandbox_result(status="succeeded", exit_code=0, version="3.12.3", completion_marker="__PAI_TEST_COMPLETED__:test-token"):
    return {
        "execution_status": status,
        "exit_code": exit_code,
        "stdout": f"__PAI_PYTHON_VERSION__:{version}\nassertion harness completed\n{completion_marker}\n",
        "stderr": "",
        "duration_ms": 23,
        "image": "python:3.12-slim@sha256:" + "a" * 64,
        "source_sha256": "sha256:wrapped-source",
    }


def _completion_marker(wrapper_source):
    match = re.search(r"__PAI_TEST_COMPLETED__:[0-9a-f]{32}", wrapper_source)
    assert match is not None
    return match.group(0)


def test_successful_exact_version_sandbox_run_promotes_l1_to_l2(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    captured = {}

    def sandbox_handler(payload, checkpoint, context):
        captured["payload"] = payload
        return _sandbox_result(completion_marker=_completion_marker(payload["source_code"]))

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert semantics",
        "source_code": "assert (1 + 1) == 2",
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert (1 + 1) == 2"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }, None, FakeContext())

    assert result["outcome"] == "passed"
    assert result["evidence_level_promoted"] is True
    assert result["runtime_version"] == "3.12.3"
    assert "__PAI_PYTHON_VERSION__" in captured["payload"]["source_code"]
    assert "assert (1 + 1) == 2" in captured["payload"]["source_code"]
    assert result["test_source_sha256"]
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        test = connection.execute(
            "SELECT outcome, environment_name, environment_version, command_hash, log_hash, result_summary FROM factual_tests WHERE knowledge_id=?",
            (knowledge_id,),
        ).fetchone()
    assert tuple(claim) == ("L2_tested", "active")
    assert test["outcome"] == "passed"
    assert test["environment_name"] == "python"
    assert test["environment_version"] == "3.12.3"
    assert test["command_hash"] == result["test_source_sha256"]
    assert test["log_hash"] == result["log_sha256"]
    summary = json.loads(test["result_summary"])
    assert summary["runner"] == "docker-sandbox"
    assert "assertion harness completed" not in test["result_summary"]


def test_sandbox_version_mismatch_records_environment_failure_without_promotion(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim(version="3.12.3")
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    handler = factual_verification_tasks._build_handler(
        lambda payload, *_args: _sandbox_result(version="3.13.0", completion_marker=_completion_marker(payload["source_code"]))
    )
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert semantics",
        "source_code": "assert True",
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert True"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }, None, FakeContext())

    assert result["outcome"] == "environment_failure"
    assert result["evidence_level_promoted"] is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()
        outcome = connection.execute(
            "SELECT outcome FROM factual_tests WHERE knowledge_id=?", (knowledge_id,)
        ).fetchone()[0]
    assert claim["evidence_level"] == "L1_sourced"
    assert outcome == "environment_failure"


def test_failed_assertion_is_inconclusive_not_automatic_claim_failure(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    handler = factual_verification_tasks._build_handler(
        lambda payload, *_args: _sandbox_result(status="failed", exit_code=1, completion_marker=_completion_marker(payload["source_code"]))
    )
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert semantics",
        "source_code": "assert False",
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert False"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }, None, FakeContext())

    assert result["outcome"] == "inconclusive"
    assert result["evidence_level_promoted"] is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        outcome = connection.execute(
            "SELECT outcome FROM factual_tests WHERE knowledge_id=?", (knowledge_id,)
        ).fetchone()[0]
    assert tuple(claim) == ("L1_sourced", "active")
    assert outcome == "inconclusive"


def test_factual_claim_sandbox_runner_requires_current_consent(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: False)
    called = False

    def sandbox_handler(*_args):
        nonlocal called
        called = True
        return _sandbox_result()

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    with pytest.raises(ValueError, match="consent"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "assert semantics",
            "source_code": "assert True",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
            "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert True"),
            "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        }, None, FakeContext())
    assert called is False


def test_factual_claim_route_queues_only_explicitly_confirmed_tests(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(factual_verification_routes.sandbox_consent, "get_consent", lambda: {"enabled": True})
    monkeypatch.setattr(factual_verification_routes, "_configured", lambda: True)
    captured = {}

    def create_task(task_type, payload, **kwargs):
        captured.update(task_type=task_type, payload=payload, kwargs=kwargs)
        return {"id": 77, "status": "queued"}

    monkeypatch.setattr(factual_verification_routes.task_manager, "create_task", create_task)
    request = factual_verification_routes.FactualClaimTestRequest(
        confirm=True,
        oracle_reviewed=True,
        oracle_review_note="Compared the assertion against the official Python reference for assert semantics.",
        oracle_source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
        oracle_source_hash=hashlib.sha256(b"reference excerpt").hexdigest(),
        test_name="assert semantics",
        source_code="assert True",
        timeout_seconds=5,
        max_output_bytes=1000,
    )
    result = factual_verification_routes.queue_factual_claim_test(knowledge_id, request)

    assert result["task"]["id"] == 77
    assert captured["task_type"] == factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE
    assert captured["kwargs"]["max_attempts"] == 1
    assert captured["payload"]["knowledge_id"] == knowledge_id
    assert captured["kwargs"]["dedupe_key"].startswith(f"factual-verify:{knowledge_id}:")


def test_factual_claim_test_harness_requires_assertion():
    with pytest.raises(ValueError, match="assert statement"):
        factual_verification_tasks.validate_test_harness("print('not a test')")



def test_claim_change_during_execution_prevents_promotion(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)

    def sandbox_handler(payload, *_args):
        with db.learning_connection() as connection:
            factual_knowledge.transition_lifecycle(connection, knowledge_id, "needs_review")
        return _sandbox_result(completion_marker=_completion_marker(payload["source_code"]))

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert semantics",
        "source_code": "assert True",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert True"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
    }, None, FakeContext())

    assert result["outcome"] == "inconclusive"
    assert result["evidence_level_promoted"] is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
    assert tuple(claim) == ("L1_sourced", "needs_review")


def test_route_rejects_missing_independent_oracle_attestation():
    from fastapi import HTTPException

    request = factual_verification_routes.FactualClaimTestRequest(
        confirm=True,
        oracle_reviewed=False,
        oracle_review_note="This note is long enough for schema validation.",
        oracle_source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
        oracle_source_hash=hashlib.sha256(b"reference excerpt").hexdigest(),
        test_name="assert semantics",
        source_code="assert True",
    )
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.queue_factual_claim_test(1, request)
    assert raised.value.status_code == 422



def test_zero_exit_without_completion_marker_cannot_promote_claim(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    handler = factual_verification_tasks._build_handler(
        lambda *_args: _sandbox_result(completion_marker="__PAI_TEST_COMPLETED__:not-the-run-marker")
    )
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert semantics",
        "source_code": "assert True",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert True"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
    }, None, FakeContext())

    assert result["outcome"] == "inconclusive"
    assert result["evidence_level_promoted"] is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()
    assert claim["evidence_level"] == "L1_sourced"


def test_factual_test_queue_lists_only_active_sourced_python_l1_without_passing_test(learning_store):
    knowledge_id = _active_sourced_python_claim()
    result = factual_verification_routes.list_factual_test_queue(limit=10)
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == knowledge_id
    assert item["domain_name"] == "python"
    assert item["evidence_level"] == "L1_sourced"
    assert item["lifecycle_status"] == "active"
    assert item["evidence"][0]["source_ref"].startswith("https://")

    with db.learning_connection() as connection:
        factual_knowledge.record_test(
            connection,
            knowledge_id,
            test_kind="integration",
            test_name="existing passing test",
            environment_version="3.12.3",
            outcome="passed",
            result_summary="Previously passed against the exact Python version.",
            command_hash="a" * 64,
            log_hash="b" * 64,
        )
    refreshed = factual_verification_routes.list_factual_test_queue(limit=10)
    assert refreshed["count"] == 0


def test_factual_test_queue_hides_claim_with_active_test_task(learning_store):
    knowledge_id = _active_sourced_python_claim()
    task_manager.initialize_task_store()
    assert factual_verification_routes.list_factual_test_queue(limit=50)["count"] == 1

    task_manager.create_task(
        factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE,
        {
            "knowledge_id": knowledge_id,
            "test_name": "queued test",
            "source_code": "assert True",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
            "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "assert True"),
            "oracle_review_note": "Reviewed against the official Python reference before queueing.",
        },
        max_attempts=1,
        dedupe_key="test-queue-active-task",
    )
    assert factual_verification_routes.list_factual_test_queue(limit=50)["count"] == 0


def test_factual_test_history_exposes_bounded_results_and_current_claim_state(learning_store):
    knowledge_id = _active_sourced_python_claim()
    with db.learning_connection() as connection:
        factual_knowledge.record_test(
            connection,
            knowledge_id,
            test_kind="integration",
            test_name="assert false condition",
            environment_version="3.12.3",
            outcome="claim_failed",
            result_summary="The assertion behavior did not match the expected oracle.",
            command_hash="c" * 64,
            log_hash="d" * 64,
        )
        factual_knowledge.record_test(
            connection,
            knowledge_id,
            test_kind="integration",
            test_name="assert false condition retry",
            environment_version="3.12.3",
            outcome="passed",
            result_summary="The reviewed assertion test passed.",
            command_hash="e" * 64,
            log_hash="f" * 64,
        )

    result = factual_verification_routes.list_factual_test_history(limit=1)
    assert result["count"] == 1
    item = result["items"][0]
    assert item["knowledge_id"] == knowledge_id
    assert item["claim"] == "A Python assertion raises an error when its condition is false."
    assert item["outcome"] == "passed"
    assert item["evidence_level"] == "L1_sourced"
    assert item["lifecycle_status"] == "active"
    assert item["test_environment_version"] == item["current_environment_version"] == "3.12.3"
    assert item["command_hash"] == "e" * 64
    assert item["log_hash"] == "f" * 64
