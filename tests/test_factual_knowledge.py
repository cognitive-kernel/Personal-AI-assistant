import json
import sqlite3

import pytest

from app.factual_knowledge import (
    KnowledgePolicyError,
    add_evidence,
    add_relation,
    create_claim,
    get_claim,
    list_claim_revisions,
    mark_due_claims_for_review,
    get_cached_review,
    put_cached_review,
    review_cache_key,
    record_test,
    search_retrievable_claims,
    transition_evidence_level,
    transition_lifecycle,
)
from app.schema_registry import LEARNING_MIGRATIONS
from app.schema_migrations import MigrationRunner


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 21
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


def test_claim_identity_includes_scope_and_environment_version(connection):
    first = create_claim(
        connection, "The API returns a list", environment_version="3.11",
        scope={"platform": "linux"},
    )
    second = create_claim(
        connection, "The API returns a list", environment_version="3.11",
        scope={"platform": "windows"},
    )
    third = create_claim(
        connection, "The API returns a list", environment_version="3.12",
        scope={"platform": "linux"},
    )
    assert len({first, second, third}) == 3
    hashes = connection.execute(
        "SELECT claim_hash FROM factual_knowledge WHERE id IN (?, ?, ?)",
        (first, second, third),
    ).fetchall()
    assert len({row[0] for row in hashes}) == 3


def test_claim_and_evidence_writes_are_idempotent(connection):
    first_id = make_claim(connection, "Python dictionaries preserve key-value associations")
    second_id = make_claim(connection, "Python dictionaries preserve key-value associations")
    assert first_id == second_id

    first_evidence = source(connection, first_id)
    second_evidence = source(connection, first_id)
    assert first_evidence == second_evidence
    assert connection.execute(
        "SELECT COUNT(*) FROM factual_knowledge WHERE id=?", (first_id,)
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM factual_evidence WHERE knowledge_id=?", (first_id,)
    ).fetchone()[0] == 1


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
    with pytest.raises(sqlite3.IntegrityError):
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
    with pytest.raises(sqlite3.IntegrityError, match="high-risk knowledge requires human verification"):
        connection.execute("UPDATE factual_knowledge SET lifecycle_status='active' WHERE id=?", (kid,))
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



def test_review_cache_is_exact_context_versioned_and_expires(connection):
    common = dict(
        normalized_claim="Python lists preserve order",
        source_hash="sha256:source-a",
        model_version="model-a",
        prompt_version="prompt-1",
        scope={"python": "3.11"},
    )
    key = review_cache_key(**common)
    assert key == review_cache_key(**common)
    assert key != review_cache_key(**{**common, "model_version": "model-b"})
    assert key != review_cache_key(**{**common, "source_hash": "sha256:source-b"})
    put_cached_review(
        connection,
        cache_key=key,
        claim_hash="sha256:claim",
        source_hash=common["source_hash"],
        model_version=common["model_version"],
        prompt_version=common["prompt_version"],
        scope_hash="sha256:scope",
        result={"judgment": "supported", "confidence": 0.8},
        ttl_seconds=3600,
    )
    assert get_cached_review(connection, key) == {"judgment": "supported", "confidence": 0.8}
    connection.execute(
        "UPDATE factual_review_cache SET expires_at = datetime('now', '-1 second') WHERE cache_key = ?",
        (key,),
    )
    assert get_cached_review(connection, key) is None
    assert connection.execute(
        "SELECT 1 FROM factual_review_cache WHERE cache_key = ?", (key,)
    ).fetchone() is None


def test_review_cache_rejects_unsafe_ttl_and_non_json_values(connection):
    with pytest.raises(KnowledgePolicyError, match="ttl_seconds"):
        put_cached_review(
            connection, cache_key="key", claim_hash="claim", source_hash="source",
            model_version="model", prompt_version="prompt", scope_hash="scope",
            result={"ok": True}, ttl_seconds=0,
        )
    with pytest.raises(ValueError):
        put_cached_review(
            connection, cache_key="key", claim_hash="claim", source_hash="source",
            model_version="model", prompt_version="prompt", scope_hash="scope",
            result={"bad": float("nan")},
        )



def test_claim_revision_history_tracks_content_not_lifecycle_changes(connection):
    kid = make_claim(connection, "Python lists are ordered")
    revisions = list_claim_revisions(connection, kid)
    assert len(revisions) == 1
    assert revisions[0]["revision_number"] == 1
    assert revisions[0]["claim"] == "Python lists are ordered"

    connection.execute(
        """UPDATE factual_knowledge
           SET claim=?, normalized_claim=?, claim_hash=?, scope_json=?
           WHERE id=?""",
        (
            "Python lists preserve insertion order",
            "Python lists preserve insertion order",
            "updated-content-hash",
            '{"topic":"sequence"}',
            kid,
        ),
    )
    revisions = list_claim_revisions(connection, kid)
    assert [item["revision_number"] for item in revisions] == [1, 2]
    assert revisions[1]["claim"] == "Python lists preserve insertion order"
    assert revisions[1]["scope_json"] == '{"topic":"sequence"}'

    connection.execute(
        "UPDATE factual_knowledge SET lifecycle_status='needs_review' WHERE id=?",
        (kid,),
    )
    assert len(list_claim_revisions(connection, kid)) == 2

    source(connection, kid)
    transition_evidence_level(connection, kid, "L1_sourced")
    assert len(list_claim_revisions(connection, kid)) == 2

    with pytest.raises(sqlite3.IntegrityError, match="revisions are immutable"):
        connection.execute(
            "UPDATE factual_claim_revisions SET claim='tampered' WHERE knowledge_id=? AND revision_number=1",
            (kid,),
        )

    with pytest.raises(sqlite3.IntegrityError, match="revisions are immutable"):
        connection.execute(
            "DELETE FROM factual_claim_revisions WHERE knowledge_id=? AND revision_number=1",
            (kid,),
        )


def test_migration_seeds_existing_claims_as_revision_one():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        assert MigrationRunner(LEARNING_MIGRATIONS[:-4]).apply(conn) == 16
        old_id = conn.execute(
            """INSERT INTO factual_knowledge(
                   claim, normalized_claim, claim_hash, scope_json,
                   environment_name, environment_version, risk_level
               ) VALUES (?, ?, ?, '{}', 'python', '3.11', 'low')""",
            (
                "Existing claim before revision migration",
                "Existing claim before revision migration",
                "a" * 64,
            ),
        ).lastrowid
        domain_id = conn.execute(
            """INSERT INTO factual_knowledge(
                   claim, normalized_claim, claim_hash, scope_json,
                   environment_name, environment_version, risk_level
               ) VALUES (?, ?, ?, '{}', 'sql_server', 'unspecified', 'low')""",
            (
                "Legacy SQL Server claim",
                "Legacy SQL Server claim",
                "b" * 64,
            ),
        ).lastrowid
        conn.commit()
        assert MigrationRunner(LEARNING_MIGRATIONS[:-3]).apply(conn) == 17
        legacy_revision = conn.execute(
            "SELECT revision_number, claim FROM factual_claim_revisions WHERE knowledge_id=?",
            (old_id,),
        ).fetchall()
        assert legacy_revision == [(1, "Existing claim before revision migration")]
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 20
        revisions = list_claim_revisions(conn, old_id)
        assert len(revisions) == 1
        assert revisions[0]["revision_number"] == 1
        assert revisions[0]["claim"] == "Existing claim before revision migration"
        assert revisions[0]["domain_name"] == "python"
        domain_revision = list_claim_revisions(conn, domain_id)
        assert len(domain_revision) == 1
        assert domain_revision[0]["domain_name"] == "sql_server"
        assert conn.execute(
            "SELECT domain_name FROM factual_knowledge WHERE id=?", (domain_id,)
        ).fetchone()[0] == "sql_server"
    finally:
        conn.close()


def test_claim_identity_isolated_by_environment_name(connection):
    python_claim = create_claim(
        connection, "The optimizer chooses an execution plan",
        environment_name="python", environment_version="unspecified",
    )
    sql_claim = create_claim(
        connection, "The optimizer chooses an execution plan",
        environment_name="sql_server", environment_version="unspecified",
    )
    assert python_claim != sql_claim
    rows = connection.execute(
        "SELECT id, domain_name, environment_name FROM factual_knowledge WHERE id IN (?, ?)",
        (python_claim, sql_claim),
    ).fetchall()
    assert {(row[0], row[1], row[2]) for row in rows} == {
        (python_claim, "python", "python"), (sql_claim, "sql_server", "sql_server")
    }


def test_claim_rejects_invalid_environment_name(connection):
    with pytest.raises(KnowledgePolicyError, match="normalized domain identifier"):
        create_claim(
            connection, "A claim", environment_name="SQL Server",
            environment_version="unspecified",
        )

def _active_tested_claim(
    connection, claim, *, environment_name="sql_server", environment_version="2022",
    level="L2_tested", risk_level="low",
):
    connection.row_factory = sqlite3.Row
    knowledge_id = create_claim(
        connection,
        claim,
        environment_name=environment_name,
        environment_version=environment_version,
        scope={"topic": claim},
        risk_level=risk_level,
    )
    add_evidence(
        connection,
        knowledge_id,
        source_type="official_docs",
        source_ref=f"https://learn.microsoft.com/test/{knowledge_id}",
        source_hash=f"sha256:test-source-{knowledge_id}",
        excerpt=claim,
        applicability={"environment_name": environment_name, "environment_version": environment_version},
    )
    transition_evidence_level(connection, knowledge_id, "L1_sourced")
    record_test(
        connection,
        knowledge_id,
        test_kind="unit",
        test_name=f"test_claim_{knowledge_id}",
        environment_name=environment_name,
        environment_version=environment_version,
        outcome="passed",
        result_summary="Deterministic test fixture.",
    )
    transition_evidence_level(connection, knowledge_id, "L2_tested")
    if level == "L3_human_verified":
        transition_evidence_level(connection, knowledge_id, "L3_human_verified", human_verified_by="test-reviewer")
    transition_lifecycle(connection, knowledge_id, "active")
    return knowledge_id


def test_verified_retrieval_returns_only_active_source_backed_exact_scope(connection):
    l2_id = _active_tested_claim(connection, "SQL Server query plans expose selected operations")
    l3_id = _active_tested_claim(
        connection, "SQL Server indexes can reduce reads", level="L3_human_verified"
    )
    _active_tested_claim(
        connection, "SQL Server query plans for a different version",
        environment_version="2025",
    )
    test_only_id = _active_tested_claim(
        connection, "SQL Server claim supported only by test output",
    )
    connection.execute("DELETE FROM factual_evidence WHERE knowledge_id=?", (test_only_id,))
    add_evidence(
        connection, test_only_id, source_type="test_output",
        source_ref=f"test-run:{test_only_id}", source_hash=f"sha256:test-only-{test_only_id}",
        excerpt="A passing test without source provenance.",
    )
    results = search_retrievable_claims(
        connection,
        "SQL Server",
        domain="sql_server",
        environment_name="sql_server",
        environment_version="2022",
    )
    assert {item["id"] for item in results} == {l2_id, l3_id}
    assert results[0]["evidence_level"] == "L3_human_verified"
    assert results[0]["lifecycle_status"] == "active"
    assert results[0]["evidence"][0]["source_type"] == "official_docs"
    assert all(item["environment_version"] == "2022" for item in results)


def test_verified_retrieval_excludes_unverified_failed_conflicting_superseded_and_l1(connection):
    _active_tested_claim(connection, "SQL Server active verified query plans")
    unverified = create_claim(
        connection, "SQL Server unverified query plans",
        environment_name="sql_server", environment_version="2022",
    )
    failed = create_claim(
        connection, "SQL Server failed query plans",
        environment_name="sql_server", environment_version="2022",
    )
    transition_lifecycle(connection, failed, "failed")
    conflicting = _active_tested_claim(connection, "SQL Server conflicting query plans")
    transition_lifecycle(connection, conflicting, "conflicting")
    superseded = _active_tested_claim(connection, "SQL Server superseded query plans")
    transition_lifecycle(connection, superseded, "superseded")
    l1 = create_claim(
        connection, "SQL Server sourced but untested query plans",
        environment_name="sql_server", environment_version="2022",
    )
    add_evidence(
        connection, l1, source_type="official_docs",
        source_ref="https://learn.microsoft.com/test/l1",
        source_hash="sha256:l1", excerpt="Sourced, not tested.",
    )
    transition_evidence_level(connection, l1, "L1_sourced")
    transition_lifecycle(connection, l1, "active")

    results = search_retrievable_claims(
        connection,
        "SQL Server query plans",
        domain="sql_server",
        environment_name="sql_server",
        environment_version="2022",
    )
    ids = {item["id"] for item in results}
    assert unverified not in ids
    assert failed not in ids
    assert conflicting not in ids
    assert superseded not in ids
    assert l1 not in ids


def test_verified_retrieval_excludes_both_sides_of_active_contradiction(connection):
    first = _active_tested_claim(connection, "SQL Server isolation level allows phantom reads")
    second = _active_tested_claim(connection, "SQL Server isolation level prevents phantom reads")
    add_relation(connection, first, second, "contradicts")
    results = search_retrievable_claims(
        connection,
        "SQL Server isolation level phantom reads",
        domain="sql_server",
        environment_name="sql_server",
        environment_version="2022",
    )
    assert first not in {item["id"] for item in results}
    assert second not in {item["id"] for item in results}


def test_verified_retrieval_fails_closed_without_exact_environment_scope(connection):
    _active_tested_claim(connection, "SQL Server query plans expose selected operations")
    assert search_retrievable_claims(
        connection, "SQL Server query plans", environment_name="", environment_version="2022"
    ) == []
    assert search_retrievable_claims(
        connection, "SQL Server query plans", environment_name="sql_server", environment_version=""
    ) == []
    with pytest.raises(KnowledgePolicyError, match="limit must be an integer"):
        search_retrievable_claims(
            connection, "SQL Server query plans", environment_name="sql_server",
            environment_version="2022", limit=0,
        )



def _active_claim_with_reverification_date(
    connection, claim_text, due_at
):
    knowledge_id = create_claim(
        connection,
        claim_text,
        environment_name="sql_server",
        environment_version="2022",
    )
    add_evidence(
        connection,
        knowledge_id,
        source_type="official_docs",
        source_ref=f"https://learn.microsoft.com/sql/test/{knowledge_id}",
        source_hash=f"sha256:reverify-{knowledge_id}",
        excerpt="A source excerpt captured for the re-verification regression test.",
    )
    transition_evidence_level(connection, knowledge_id, "L1_sourced")
    record_test(
        connection,
        knowledge_id,
        test_kind="integration",
        test_name=f"reverify-test-{knowledge_id}",
        environment_name="sql_server",
        environment_version="2022",
        outcome="passed",
        result_summary="Applicable test passed.",
    )
    transition_evidence_level(connection, knowledge_id, "L2_tested")
    transition_lifecycle(connection, knowledge_id, "active")
    connection.execute(
        "UPDATE factual_knowledge SET reverify_after=? WHERE id=?",
        (due_at, knowledge_id),
    )
    return knowledge_id


def test_due_active_claims_are_quarantined_and_audited_once(connection):
    due = _active_claim_with_reverification_date(
        connection, "SQL Server due claim", "2000-01-01 00:00:00"
    )
    future = _active_claim_with_reverification_date(
        connection, "SQL Server future claim", "2999-01-01 00:00:00"
    )
    no_deadline = _active_claim_with_reverification_date(
        connection, "SQL Server claim without deadline", None
    )

    assert mark_due_claims_for_review(connection) == 1
    assert mark_due_claims_for_review(connection) == 0

    statuses = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT id, lifecycle_status FROM factual_knowledge WHERE id IN (?, ?, ?)",
            (due, future, no_deadline),
        )
    }
    assert statuses == {
        due: "needs_review",
        future: "active",
        no_deadline: "active",
    }
    event = connection.execute(
        """SELECT event_type, status_before, status_after, due_at,
                  source_hashes_json, reason
           FROM factual_knowledge_review_events WHERE knowledge_id=?""",
        (due,),
    ).fetchone()
    assert event[0:4] == (
        "reverification_due", "active", "needs_review", "2000-01-01 00:00:00"
    )
    assert json.loads(event[4]) == [f"sha256:reverify-{due}"]
    assert "excluded from retrieval" in event[5]
    assert connection.execute(
        "SELECT COUNT(*) FROM factual_knowledge_review_events WHERE knowledge_id=?",
        (due,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("limit", [0, -1, 501, True])
def test_due_claim_review_rejects_invalid_batch_limits(connection, limit):
    with pytest.raises(KnowledgePolicyError, match="limit"):
        mark_due_claims_for_review(connection, limit=limit)
