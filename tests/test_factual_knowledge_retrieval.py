import sqlite3

import pytest

from app.factual_knowledge import (
    KnowledgePolicyError,
    add_evidence,
    create_claim,
    list_retrievable_claims,
    record_test,
    transition_evidence_level,
    transition_lifecycle,
)
from app.schema_migrations import MigrationRunner
from app.schema_registry import LEARNING_MIGRATIONS


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 16
    yield conn
    conn.close()


def make_claim(connection, text, *, level="L2_tested"):
    kid = create_claim(connection, text, environment_version="3.11", risk_level="low")
    add_evidence(
        connection,
        kid,
        source_type="official_docs",
        source_ref="https://docs.python.org/3/library/stdtypes.html",
        source_hash=f"sha256:{text}",
        excerpt="Primary source excerpt.",
    )
    transition_evidence_level(connection, kid, "L1_sourced")
    record_test(
        connection,
        kid,
        test_kind="unit",
        test_name="claim-regression",
        environment_version="3.11",
        outcome="passed",
        result_summary="Expected behavior observed.",
    )
    transition_evidence_level(connection, kid, "L2_tested")
    if level == "L3_human_verified":
        transition_evidence_level(
            connection, kid, "L3_human_verified", human_verified_by="reviewer-1"
        )
    transition_lifecycle(connection, kid, "active")
    return kid


def test_retrieval_returns_only_active_source_backed_l2_l3_claims(connection):
    l2_id = make_claim(connection, "Python list behavior is tested.")
    l3_id = make_claim(
        connection, "Python tuple behavior is human verified.", level="L3_human_verified"
    )

    raw_id = create_claim(connection, "Raw claim must not be retrieved.", environment_version="3.11")
    l1_id = create_claim(connection, "Sourced only claim must not be retrieved.", environment_version="3.11")
    add_evidence(
        connection, l1_id, source_type="official_docs",
        source_ref="https://docs.python.org/3/library/stdtypes.html",
        source_hash="sha256:l1", excerpt="Source excerpt.",
    )
    transition_evidence_level(connection, l1_id, "L1_sourced")
    transition_lifecycle(connection, l1_id, "active")

    failed_id = make_claim(connection, "Failed claim must not be retrieved.")
    transition_lifecycle(connection, failed_id, "failed")
    conflicting_id = make_claim(connection, "Conflicting claim must not be retrieved.")
    transition_lifecycle(connection, conflicting_id, "conflicting")
    superseded_id = make_claim(connection, "Superseded claim must not be retrieved.")
    transition_lifecycle(connection, superseded_id, "superseded")
    stale_id = make_claim(connection, "Expired claim must not be retrieved.")
    connection.execute(
        "UPDATE factual_knowledge SET reverify_after=datetime('now', '-1 second') WHERE id=?",
        (stale_id,),
    )

    results = list_retrievable_claims(connection)
    ids = {item["id"] for item in results}

    assert ids == {l2_id, l3_id}
    assert raw_id not in ids
    assert l1_id not in ids
    assert failed_id not in ids
    assert conflicting_id not in ids
    assert superseded_id not in ids
    assert stale_id not in ids
    assert results[0]["id"] == l3_id
    assert results[0]["evidence"][0]["source_ref"].startswith("https://docs.python.org/")
    assert results[0]["evidence"][0]["applicability"] == {}
    assert results[0]["passing_tests"][0]["outcome"] == "passed"


def test_retrieval_limit_is_bounded_and_strict(connection):
    make_claim(connection, "A valid claim.")

    for value in (0, -1, 501, True, 1.5):
        with pytest.raises(KnowledgePolicyError, match="limit must be an integer"):
            list_retrievable_claims(connection, limit=value)

    assert len(list_retrievable_claims(connection, limit=1)) == 1


def test_retrieval_does_not_return_claim_without_applicable_passing_test(connection):
    kid = create_claim(connection, "Claim has source but no passing test.", environment_version="3.11")
    add_evidence(
        connection, kid, source_type="official_docs",
        source_ref="https://docs.python.org/3/library/stdtypes.html",
        source_hash="sha256:no-test", excerpt="Source excerpt.",
    )
    transition_evidence_level(connection, kid, "L1_sourced")
    # The schema will not permit L2 without an applicable passing test; L1 is
    # deliberately activated here to prove retrieval still applies its own gate.
    transition_lifecycle(connection, kid, "active")
    assert list_retrievable_claims(connection) == []
