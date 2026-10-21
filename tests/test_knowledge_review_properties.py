"""Property-based invariants for the hybrid knowledge evidence index."""
import hashlib
import json

from hypothesis import given, settings, strategies as st

from app import db, learning
from app.knowledge_review import (
    calculate_knowledge_confidence,
    record_peer_consultation,
    record_review_evidence,
    record_user_application_feedback,
)


def _candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("property-based evidence scoring", source_text="original source")
    lesson = "A temporary lesson whose evidence score is under test."
    digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_test')""",
            (job["id"], job["topic"], lesson, "user_provided_text", "[]",
             digest, json.dumps({"accepted": True})),
        )
    return int(cursor.lastrowid)


@given(
    evidence_kinds=st.lists(
        st.sampled_from(["supporting", "contradicting", "context", "application"]),
        max_size=8,
    ),
    peer_stances=st.lists(
        st.sampled_from(["supports", "contradicts", "mixed", "insufficient_evidence"]),
        max_size=5,
    ),
    outcomes=st.lists(
        st.sampled_from(["worked", "failed", "partial", "not_tested"]),
        max_size=5,
    ),
)
@settings(max_examples=12, deadline=None)
def test_evidence_score_is_bounded_and_versioned(
    tmp_path, monkeypatch, evidence_kinds, peer_stances, outcomes
):
    candidate_id = _candidate(tmp_path, monkeypatch)

    for index, kind in enumerate(evidence_kinds):
        record_review_evidence(
            candidate_id,
            evidence_kind=kind,
            source_url=f"https://source-{index}.example.org/claim",
            source_title=f"Source {index}",
            excerpt=f"Distinct evidence excerpt {index} for the candidate.",
        )
    for index, stance in enumerate(peer_stances):
        record_peer_consultation(
            candidate_id,
            provider=f"provider-{index}",
            model_name="test-model",
            prompt=f"Challenge the claim, review {index}.",
            response=f"Independent review response {index}: {stance}.",
            stance=stance,
        )
    for index, outcome in enumerate(outcomes):
        record_user_application_feedback(
            candidate_id,
            outcome=outcome,
            application_context=f"Application context {index}",
            observed_output=f"Observed result {index}",
            test_steps=f"Reproduction steps {index}",
        )

    score = calculate_knowledge_confidence(candidate_id)
    assert 0 <= score["score"] <= 100
    assert score["score_version"] == "knowledge-evidence-v1"
    assert score["score_scale"].endswith("not a calibrated probability of truth")
    assert score["verification_status"] != "tested_in_declared_scope"


def test_context_only_web_retrieval_does_not_increase_support_score(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    baseline = calculate_knowledge_confidence(candidate_id)["score"]
    record_review_evidence(
        candidate_id,
        evidence_kind="context",
        source_url="https://retrieved.example.org/page",
        source_title="Fetched page",
        excerpt="A fetched page is context until claim support is assessed.",
    )
    after = calculate_knowledge_confidence(candidate_id)
    assert after["score"] == baseline
    assert "internet_sources" in after["verification_methods"]
    assert after["counts"]["supporting_domains"] == 0
