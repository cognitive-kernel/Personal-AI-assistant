import sqlite3

import pytest

from app.factual_knowledge import (
    KnowledgePolicyError,
    add_evidence,
    add_relation,
    create_claim,
    get_claim,
    record_test,
    transition_evidence_level,
    transition_lifecycle,
)
from app.schema_registry import LEARNING_MIGRATIONS
from app.schema_migrations import MigrationRunner


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 16
    yield conn
    conn.close()


def make_claim(conn, claim="Python lists are ordered", risk="low"):
    return create_claim(conn, claim, environment_version="3.11", risk_level=risk)


def source(conn, knowledge_id):
    return add_evidence(
        conn, knowledge_id, source_type="official_docs",
        source_ref="https://docs.python.org/3/library/stdtypes.html",
        source_hash="sha256:source-1", excerpt="Lists are mutable sequences.",
    )


def test_fresh_schema_has_separate_knowledge_evidence_tests_and_relations(connection):
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"factual_knowledge", "factual_evidence", "factual_tests", "knowledge_relations"} <= tables
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_cannot_jump_from_l0_to_l2_or_l3(connection):
    kid = make_claim(connection)
    with pytest.raises(KnowledgePolicyError, match="invalid evidence-level transition"):
        transition_evidence_level(connection, kid, "L2_tested")
    with pytest.raises(KnowledgePolicyError, match="invalid evidence-level transition"):
        transition_evidence_level(connection, kid, "L3_human_verified")
    with pytest.raises(sqlite3.IntegrityError, match="invalid factual knowledge evidence-level transition"):
        connection.execute("UPDATE factual_knowledge SET evidence_level='L3_human_verified' WHERE id=?", (kid,))


def test_l1_requires_source_and_l2_requires_applicable_passing_test(connection):
    kid = make_claim(connection)
    with pytest.raises(sqlite3.IntegrityError, match="traceable evidence"):
        connection.execute("UPDATE factual_knowledge SET evidence_level='L1_sourced' WHERE id=?", (kid,))
    source(connection, kid)
    transition_evidence_level(connection, kid, "L1_sourced")
    with pytest.raises(sqlite3.IntegrityError, match="passing applicable test"):
        connection.execute("UPDATE factual_knowledge SET evidence_level='L2_tested' WHERE id=?", (kid,))
    record_test(
        connection, kid, test_kind="unit", test_name="list-order",
        environment_version="3.10", outcome="passed",
    )
    with pytest.raises(sqlite3.IntegrityError, match="passing applicable test"):
        connection.execute("UPDATE factual_knowledge SET evidence_level='L2_tested' WHERE id=?", (kid,))
    record_test(
        connection, kid, test_kind="unit", test_name="list-order",
        environment_version="3.11", outcome="passed",
    )
    transition_evidence_level(connection, kid, "L2_tested")
    assert get_claim(connection, kid).evidence_level == "L2_tested"


def test_human_verification_requires_explicit_reviewer_and_high_risk_activation(connection):
    kid = make_claim(connection, risk="critical")
    source(connection, kid)
    transition_evidence_level(connection, kid, "L1_sourced")
    record_test(
        connection, kid, test_kind="unit", test_name="critical-claim",
        environment_version="3.11", outcome="passed",
    )
    transition_evidence_level(connection, kid, "L2_tested")
    with pytest.raises(KnowledgePolicyError, match="human verification"):
        transition_lifecycle(connection, kid, "active")
    with pytest.raises(KnowledgePolicyError, match="human_verified_by"):
        transition_evidence_level(connection, kid, "L3_human_verified")
    transition_evidence_level(connection, kid, "L3_human_verified", human_verified_by="reviewer-1")
    transition_lifecycle(connection, kid, "active")
    assert get_claim(connection, kid).lifecycle_status == "active"


def test_lifecycle_guards_and_relation_integrity(connection):
    raw = make_claim(connection)
    with pytest.raises(KnowledgePolicyError, match="raw knowledge"):
        transition_lifecycle(connection, raw, "active")
    with pytest.raises(sqlite3.IntegrityError, match="invalid factual knowledge lifecycle transition"):
        connection.execute("UPDATE factual_knowledge SET lifecycle_status='superseded' WHERE id=?", (raw,))

    first = make_claim(connection, "Claim A")
    second = make_claim(connection, "Claim B")
    source(connection, first)
    transition_evidence_level(connection, first, "L1_sourced")
    transition_lifecycle(connection, first, "active")
    add_relation(connection, first, second, "contradicts")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        add_relation(connection, first, first, "depends_on")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        add_relation(connection, first, second, "contradicts")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        add_relation(connection, first, 99999, "depends_on")


def test_test_failure_classes_are_distinct_and_claim_normalization_is_conservative(connection):
    kid = create_claim(connection, "  Python   preserves list order. ", environment_version="3.11")
    assert get_claim(connection, kid).claim == "Python   preserves list order."
    record_test(
        connection, kid, test_kind="unit", test_name="environment-check",
        environment_version="3.11", outcome="environment_failure",
        result_summary="interpreter unavailable",
    )
    outcomes = {row[0] for row in connection.execute(
        "SELECT outcome FROM factual_tests WHERE knowledge_id=?", (kid,)
    )}
    assert outcomes == {"environment_failure"}
