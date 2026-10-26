import hashlib
import json
import re

import pytest

from app import db, factual_knowledge, factual_semantic_review, factual_verification_routes, factual_verification_tasks, sandbox_consent, task_manager


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
    monkeypatch.setattr(
        factual_semantic_review,
        "review_claim_against_source",
        lambda **_kwargs: factual_semantic_review.ReviewDecision(
            verdict="supports",
            accepted=True,
            score=1.0,
            rubric_version=factual_semantic_review.RUBRIC_VERSION,
            reasons=("test_stub_independent_reviewer",),
            evaluator_model="test-reviewer",
            evaluator_digest="b" * 64,
        ),
    )
    monkeypatch.setattr(
        factual_verification_tasks,
        "_verify_live_source_excerpt",
        lambda _url, excerpt: hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    )
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
            source_ref="https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement",
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
            oracle_source_url="https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement",
            oracle_source_hash=hashlib.sha256(
                b"The assert statement raises AssertionError when the expression is false."
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
        "source_code": "try:\n    assert False\nexcept AssertionError:\n    pass\nelse:\n    raise AssertionError('assert did not raise')",
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, "try:\n    assert False\nexcept AssertionError:\n    pass\nelse:\n    raise AssertionError('assert did not raise')"),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }, None, FakeContext())

    assert result["outcome"] == "passed"
    assert result["evidence_level_promoted"] is True
    assert result["runtime_version"] == "3.12.3"
    assert "__PAI_PYTHON_VERSION__" in captured["payload"]["source_code"]
    assert "assert False" in captured["payload"]["source_code"]
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
    monkeypatch.setattr(factual_verification_routes, "_configured", lambda *_args: True)
    monkeypatch.setattr(factual_verification_routes, "_reviewer_configured", lambda: True)
    captured = {}

    def create_task(task_type, payload, **kwargs):
        captured.update(task_type=task_type, payload=payload, kwargs=kwargs)
        return {"id": 77, "status": "queued"}

    monkeypatch.setattr(factual_verification_routes.task_manager, "create_task", create_task)
    request = factual_verification_routes.FactualClaimTestRequest(
        confirm=True,
        oracle_reviewed=True,
        oracle_review_note="Compared the assertion against the official Python reference for assert semantics.",
        oracle_source_url="https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement",
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
        oracle_source_url="https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement",
        oracle_source_hash=hashlib.sha256(b"reference excerpt").hexdigest(),
        test_name="assert semantics",
        source_code="assert True",
    )
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.queue_factual_claim_test(1, request)
    assert raised.value.status_code == 422



def test_missing_or_mismatched_oracle_audit_cannot_execute_or_promote(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    called = {"sandbox": False}

    def sandbox_handler(*_args):
        called["sandbox"] = True
        return _sandbox_result()

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    with pytest.raises(ValueError, match="Persisted independent-oracle review"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "assert semantics",
            "source_code": "assert True",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
            "oracle_reviewed": True,
            "oracle_review_note": "Compared against the official reference documentation.",
            "oracle_review_id": 999999,
        }, None, FakeContext())
    assert called["sandbox"] is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()
    assert claim["evidence_level"] == "L1_sourced"


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

def test_independent_reviewer_rejection_cannot_execute_or_promote(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(
        factual_semantic_review,
        "review_claim_against_source",
        lambda **_kwargs: factual_semantic_review.ReviewDecision(
            verdict="insufficient_evidence",
            accepted=False,
            score=0.5,
            rubric_version=factual_semantic_review.RUBRIC_VERSION,
            reasons=("test_does_not_directly_check_claim", "fail_closed_no_promotion"),
            evaluator_model="test-reviewer",
            evaluator_digest="b" * 64,
        ),
    )
    called = False

    def sandbox_handler(*_args):
        nonlocal called
        called = True
        return _sandbox_result()

    source_code = "assert True"
    handler = factual_verification_tasks._build_handler(sandbox_handler)
    with pytest.raises(ValueError, match="did not pass"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "unrelated assertion",
            "source_code": source_code,
            "oracle_reviewed": True,
            "oracle_review_id": _oracle_review_id(knowledge_id, source_code),
            "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        }, None, FakeContext())
    assert called is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()
    assert claim["evidence_level"] == "L1_sourced"


def test_independent_reviewer_receives_exact_claim_source_and_test(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    observed = {}
    candidate_before = factual_verification_tasks.get_verification_candidate(knowledge_id)

    def review(**kwargs):
        observed.update(kwargs)
        return factual_semantic_review.ReviewDecision(
            verdict="supports",
            accepted=True,
            score=1.0,
            rubric_version=factual_semantic_review.RUBRIC_VERSION,
            reasons=("test_stub_independent_reviewer",),
            evaluator_model="test-reviewer",
            evaluator_digest="b" * 64,
        )

    monkeypatch.setattr(factual_semantic_review, "review_claim_against_source", review)
    source_code = "try:\n    assert False\nexcept AssertionError:\n    pass"
    handler = factual_verification_tasks._build_handler(
        lambda payload, *_args: _sandbox_result(completion_marker=_completion_marker(payload["source_code"]))
    )
    result = handler({
        "knowledge_id": knowledge_id,
        "test_name": "assert false condition",
        "source_code": source_code,
        "oracle_reviewed": True,
        "oracle_review_id": _oracle_review_id(knowledge_id, source_code),
        "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }, None, FakeContext())
    assert result["evidence_level_promoted"] is True
    assert observed["claim"] == "A Python assertion raises an error when its condition is false."
    assert observed["claim_hash"] == candidate_before["claim_hash"]
    assert "[Source 1] URL: https://docs.python.org/3.12" in observed["source_excerpt"]
    assert "The assert statement raises AssertionError" in observed["source_excerpt"]
    assert observed["test_source"] == source_code


def test_conflicting_evidence_is_sent_to_reviewer_and_prevents_promotion(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    with db.learning_connection() as connection:
        factual_knowledge.add_evidence(
            connection,
            knowledge_id,
            source_type="official_docs",
            source_ref="https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement",
            source_hash=hashlib.sha256(b"contradictory source").hexdigest(),
            excerpt="The assert statement never raises an exception when the expression is false.",
            applicability={"source_version": "3.12"},
        )
    called = False

    def review(**kwargs):
        nonlocal called
        called = True
        assert "The assert statement raises AssertionError" in kwargs["source_excerpt"]
        assert "The assert statement never raises an exception" in kwargs["source_excerpt"]
        return factual_semantic_review.ReviewDecision(
            verdict="conflict",
            accepted=False,
            score=0.875,
            rubric_version=factual_semantic_review.RUBRIC_VERSION,
            reasons=("unresolved_source_conflict", "fail_closed_no_promotion"),
            evaluator_model="test-reviewer",
            evaluator_digest="b" * 64,
        )

    monkeypatch.setattr(factual_semantic_review, "review_claim_against_source", review)
    source_code = "try:\n    assert False\nexcept AssertionError:\n    pass"
    handler = factual_verification_tasks._build_handler(lambda *_args: pytest.fail("sandbox must not run"))
    with pytest.raises(ValueError, match="did not pass"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "conflicting source review",
            "source_code": source_code,
            "oracle_reviewed": True,
            "oracle_review_id": _oracle_review_id(knowledge_id, source_code),
            "oracle_review_note": "Compared the assertion against the official Python reference for assert semantics.",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        }, None, FakeContext())
    assert called is True
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()
    assert claim["evidence_level"] == "L1_sourced"



@pytest.mark.parametrize("mismatch", ["url", "hash"])
def test_oracle_attestation_must_match_exact_stored_evidence(learning_store, monkeypatch, mismatch):
    knowledge_id = _active_sourced_python_claim()
    source_code = "assert True"
    candidate = factual_verification_tasks.get_verification_candidate(knowledge_id)
    with db.learning_connection() as connection:
        review_id = factual_knowledge.record_oracle_review(
            connection,
            knowledge_id,
            claim_hash=candidate["claim_hash"],
            test_source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            oracle_source_url=(
                "https://docs.python.org/3.12/reference/other.html"
                if mismatch == "url"
                else "https://docs.python.org/3.12/reference/simple_stmts.html#the-assert-statement"
            ),
            oracle_source_hash=(
                "0" * 64
                if mismatch == "hash"
                else hashlib.sha256(
                    b"The assert statement raises AssertionError when the expression is false."
                ).hexdigest()
            ),
            review_note="Compared the exact test to a reference excerpt before execution.",
        )
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    called = {"sandbox": False}

    def sandbox_handler(*_args, **_kwargs):
        called["sandbox"] = True
        return _sandbox_result()

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    with pytest.raises(ValueError, match="exact stored source excerpt"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "assert semantics",
            "source_code": source_code,
            "oracle_reviewed": True,
            "oracle_review_id": review_id,
            "oracle_review_note": "Compared the exact test to a reference excerpt before execution.",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        }, None, FakeContext())
    assert called["sandbox"] is False


def test_live_source_mismatch_blocks_sandbox_and_promotion(learning_store, monkeypatch):
    knowledge_id = _active_sourced_python_claim()
    source_code = "assert isinstance(1, int)"
    oracle_review_id = _oracle_review_id(knowledge_id, source_code)
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(
        factual_verification_tasks,
        "_verify_live_source_excerpt",
        lambda *_args: (_ for _ in ()).throw(
            ValueError("Stored evidence excerpt is absent from the current live source page.")
        ),
    )
    sandbox_called = False

    def sandbox_handler(*_args):
        nonlocal sandbox_called
        sandbox_called = True
        return _sandbox_result()

    handler = factual_verification_tasks._build_handler(sandbox_handler)
    with pytest.raises(ValueError, match="absent from the current live source page"):
        handler({
            "knowledge_id": knowledge_id,
            "test_name": "assert type",
            "source_code": source_code,
            "oracle_reviewed": True,
            "oracle_review_note": "Compared the exact test against the official Python reference excerpt.",
            "oracle_review_id": oracle_review_id,
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        }, None, FakeContext())

    assert sandbox_called is False
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        assert claim["evidence_level"] == "L1_sourced"



def test_runtime_version_selects_sqlite_runtime_for_sqlite_claims():
    from app.factual_verification_tasks import _runtime_version

    marker = "__PAI_TEST_COMPLETED__:fixed-marker"
    stdout = "\n".join([
        "__PAI_PYTHON_VERSION__:3.12.2",
        "__PAI_SQLITE_VERSION__:3.45.3",
        "sqlite assertion output",
        marker,
    ])
    version, completed, output = _runtime_version(stdout, marker, "sqlite")
    assert version == "3.45.3"
    assert completed is True
    assert output == "sqlite assertion output"


def test_wrapped_harness_reports_both_python_and_sqlite_versions():
    from app.factual_verification_tasks import _wrapped_harness

    wrapped, marker = _wrapped_harness("import sqlite3\nassert sqlite3.sqlite_version")
    assert "__PAI_PYTHON_VERSION__" in wrapped
    assert "__PAI_SQLITE_VERSION__" in wrapped
    assert marker in wrapped



@pytest.mark.parametrize(
    ("language", "source", "expected"),
    [
        ("javascript", "assert.equal(2 + 2, 4);", True),
        ("javascript", "const value = 4;", False),
        ("rust", "assert_eq!(2 + 2, 4);", True),
        ("rust", "let value = 4;", False),
    ],
)
def test_domain_harness_requires_explicit_assertions(language, source, expected):
    from app.factual_verification_tasks import _validate_domain_test_harness

    if expected:
        _validate_domain_test_harness(source, language)
    else:
        with pytest.raises(ValueError, match="assert"):
            _validate_domain_test_harness(source, language)


def test_node_and_rust_harnesses_bind_runtime_and_completion_markers():
    from app.factual_verification_tasks import _runtime_version, _wrapped_harness

    node, node_marker = _wrapped_harness("assert.equal(2 + 2, 4);", "javascript")
    assert "__PAI_NODE_VERSION__" in node
    version, completed, output = _runtime_version(
        "__PAI_NODE_VERSION__:22.14.0\nnode assertion passed\n" + node_marker,
        node_marker, "javascript",
    )
    assert version == "22.14.0"
    assert completed is True
    assert output == "node assertion passed"

    rust, rust_marker = _wrapped_harness("assert_eq!(2 + 2, 4);", "rust")
    assert "fn main()" in rust
    assert "assert_eq!(2 + 2, 4);" in rust
    version, completed, output = _runtime_version(
        "__PAI_RUST_VERSION__:1.84.0\nrust assertion passed\n" + rust_marker,
        rust_marker, "rust",
    )
    assert version == "1.84.0"
    assert completed is True
    assert output == "rust assertion passed"
