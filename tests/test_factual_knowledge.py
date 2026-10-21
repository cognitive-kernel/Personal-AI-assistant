import sqlite3

import pytest

from app.factual_knowledge import (
    KnowledgePolicyError,
    add_evidence,
    add_relation,
    create_claim,
    get_claim,
    list_claim_revisions,
    get_cached_review,
    put_cached_review,
    review_cache_key,
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
    assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 17
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

    with pytest.raises(sqlite3.IntegrityError, match="revisions are immutable"):
        connection.execute(
            "UPDATE factual_claim_revisions SET claim='tampered' WHERE knowledge_id=? AND revision_number=1",
            (kid,),
        )


def test_migration_seeds_existing_claims_as_revision_one():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        assert MigrationRunner(LEARNING_MIGRATIONS[:-1]).apply(conn) == 16
        old_id = create_claim(
            conn, "Existing claim before revision migration", environment_version="3.11"
        )
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(conn) == 17
        revisions = list_claim_revisions(conn, old_id)
        assert len(revisions) == 1
        assert revisions[0]["revision_number"] == 1
        assert revisions[0]["claim"] == "Existing claim before revision migration"
    finally:
        conn.close()
