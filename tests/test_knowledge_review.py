import hashlib
import json

import pytest

from app import db, learning
from app.knowledge_review import (
    get_candidate_review_summary,
    record_peer_consultation,
    record_review_evidence,
    research_candidate_online,
    consult_configured_peer_ais,
    calculate_knowledge_confidence,
    record_user_application_feedback,
    build_claim_verification_matrix,
    promote_candidate_after_evidence_review,
)
from app.web_sources import SourceDocument


def _candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("photosynthesis", source_text="A source for the candidate.")
    lesson = "Photosynthesis converts light energy into chemical energy."
    digest = hashlib.sha256(lesson.encode()).hexdigest()
    with db.learning_connection() as conn:
        candidate_id = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["topic"], lesson, "user_provided_text",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid
    return int(candidate_id)


def test_online_research_persists_source_as_context_not_proof(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)

    def fake_discover(query, *, limit):
        assert "photosynthesis" in query
        assert limit == 2
        return [
            SourceDocument(
                url="https://example.org/photosynthesis",
                title="Photosynthesis overview",
                text="A retrieved public page about photosynthesis.",
            )
        ]

    monkeypatch.setattr("app.web_sources.discover_sources", fake_discover)
    monkeypatch.setattr("app.claim_review.review_answer_claims", lambda *_args, **_kwargs: {
        "passed": False, "overall": "insufficient_evidence", "claims": [],
        "source_conflicts": [], "error": "insufficient_evidence",
    })
    result = research_candidate_online(candidate_id, "photosynthesis", limit=2)

    assert result["status"] == "completed"
    assert result["stored"] == 1
    with db.learning_connection() as conn:
        evidence = conn.execute(
            "SELECT evidence_kind, source_url, source_sha256, excerpt FROM knowledge_review_evidence"
        ).fetchone()
    assert evidence["evidence_kind"] == "context"
    assert evidence["source_url"] == "https://example.org/photosynthesis"
    assert len(evidence["source_sha256"]) == 64
    assert "retrieved public page" in evidence["excerpt"]
    assert get_candidate_review_summary(candidate_id)["verification_status"] == "unverified"
    plan = build_claim_verification_matrix(candidate_id)["verification_plan"]
    assert plan["version"] == "hybrid-verification-plan-v1"
    assert plan["automatic_promotion_allowed_by_this_plan"] is False
    assert "no_trusted_executable_examples_or_curated_reference_test_set" in plan["promotion_blockers"]


def test_peer_consultations_and_disagreements_are_persisted_without_verification(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    record_review_evidence(
        candidate_id,
        evidence_kind="supporting",
        source_url="https://example.org/primary",
        source_title="Primary source",
        excerpt="A reference excerpt with a defined claim.",
        reliability_note="Primary source, publication date recorded separately.",
    )
    record_review_evidence(
        candidate_id,
        evidence_kind="contradicting",
        source_url="https://example.net/counterpoint",
        source_title="Counterpoint",
        excerpt="A source that disputes the candidate claim.",
    )
    record_peer_consultation(
        candidate_id, provider="provider-a", model_name="model-a",
        prompt="Check this claim and cite sources.",
        response="The claim is broadly supported, with one limitation.",
        stance="supports", citations=[{"url": "https://example.org/primary"}],
        independence_note="Provider independence not yet established.",
    )
    record_peer_consultation(
        candidate_id, provider="provider-b", model_name="model-b",
        prompt="Try to falsify this claim.",
        response="The claim is too broad for the evidence.",
        stance="contradicts",
        independence_note="Separate provider; training-data independence unknown.",
    )

    summary = get_candidate_review_summary(candidate_id)

    assert summary["distinct_peer_providers"] == 2
    assert summary["verification_status"] == "conflicting_evidence"
    assert {item["evidence_kind"] for item in summary["evidence"]} == {"supporting", "contradicting"}
    assert {item["stance"] for item in summary["peer_consultations"]} == {"supports", "contradicts"}


def test_peer_consultation_rejects_empty_response_and_unknown_stance(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="response must not be empty"):
        record_peer_consultation(
            candidate_id, provider="provider", prompt="Review this", response=" "
        )
    with pytest.raises(ValueError, match="Unsupported consultation stance"):
        record_peer_consultation(
            candidate_id, provider="provider", prompt="Review this", response="Result",
            stance="verified",
        )
    assert get_candidate_review_summary(candidate_id)["distinct_peer_providers"] == 0


def test_online_research_failure_keeps_candidate(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)

    def fail_discover(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("app.web_sources.discover_sources", fail_discover)
    result = research_candidate_online(candidate_id, "photosynthesis")

    assert result["status"] == "unavailable"
    with db.learning_connection() as conn:
        status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"


def test_configured_peer_ai_is_queried_and_response_is_persisted(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    monkeypatch.setenv("PEER_AI_PROVIDERS_JSON", json.dumps([{
        "provider": "independent-reviewer",
        "base_url": "https://reviewer.example/v1",
        "model": "review-model",
        "api_key_env": "TEST_PEER_KEY",
    }]))
    monkeypatch.setenv("TEST_PEER_KEY", "not-persisted-secret")

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return json.dumps({
                "choices": [{"message": {"content": "The claim needs a boundary condition."}}]
            }).encode()

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://reviewer.example/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer not-persisted-secret"
        assert timeout == 15
        return FakeResponse()

    monkeypatch.setattr("app.knowledge_review.urllib.request.urlopen", fake_urlopen)
    result = consult_configured_peer_ais(candidate_id)

    assert result["status"] == "completed"
    assert result["consulted"] == 1
    summary = get_candidate_review_summary(candidate_id)
    assert summary["distinct_peer_providers"] == 1
    assert summary["verification_status"] == "evidence_reviewed_no_lab"
    with db.learning_connection() as conn:
        saved = conn.execute(
            "SELECT response, independence_note FROM knowledge_peer_consultations"
        ).fetchone()
    assert saved["response"] == "The claim needs a boundary condition."
    assert "not been established" in saved["independence_note"]
    assert "not-persisted-secret" not in saved["response"]


def test_peer_ai_endpoint_rejects_non_https_remote_hosts(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    monkeypatch.setenv("PEER_AI_PROVIDERS_JSON", json.dumps([{
        "provider": "unsafe",
        "base_url": "http://example.org/v1",
        "model": "model",
    }]))
    result = consult_configured_peer_ais(candidate_id)
    assert result["consulted"] == 0
    assert result["errors"][0]["error"] == "endpoint_must_be_https_or_loopback_without_credentials"


def test_confidence_score_tracks_distinct_methods_and_user_field_results(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    baseline = calculate_knowledge_confidence(candidate_id)
    assert baseline["score"] == 0
    assert baseline["score_version"] == "knowledge-evidence-v1"
    assert baseline["weighting"]["complete_passing_test_set_max"] == 40
    assert baseline["verification_status"] == "unverified"

    record_review_evidence(
        candidate_id, evidence_kind="supporting",
        source_url="https://docs.example.org/claim",
        source_title="Primary documentation", excerpt="Supports the exact bounded claim.",
    )
    record_peer_consultation(
        candidate_id, provider="provider-a", model_name="model-a",
        prompt="Try to falsify this", response="The bounded claim is supported.",
        stance="supports",
    )
    record_user_application_feedback(
        candidate_id, outcome="worked", application_context="Ran the example in a test project",
        observed_output="expected result", test_steps="Followed the documented steps",
    )
    after = calculate_knowledge_confidence(candidate_id)
    assert after["score"] > baseline["score"]
    assert after["verification_methods"] == ["internet_sources", "peer_ai_consultation", "real_world_user_feedback"]
    assert after["counts"]["supporting_domains"] == 1
    assert after["counts"]["supporting_peer_providers"] == 1
    assert after["counts"]["successful_user_contexts"] == 1
    assert after["verification_status"] == "evidence_reviewed_no_lab"


def test_failed_real_world_feedback_reduces_score_and_is_reported(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    record_user_application_feedback(
        candidate_id, outcome="worked", application_context="First project worked",
        observed_output="ok",
    )
    positive = calculate_knowledge_confidence(candidate_id)["score"]
    record_user_application_feedback(
        candidate_id, outcome="failed", application_context="Second project failed",
        observed_output="wrong result",
    )
    summary = get_candidate_review_summary(candidate_id)
    assert summary["confidence"]["score"] < positive
    assert {item["outcome"] for item in summary["user_application_feedback"]} == {"worked", "failed"}


def test_tested_candidate_gets_scope_limited_test_credit_not_universal_truth(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        conn.execute(
            """UPDATE temporary_knowledge_candidates
               SET status='passed', test_results_json='[{"test":"case-1","passed":true}]'
               WHERE id=?""",
            (candidate_id,),
        )
    score = calculate_knowledge_confidence(candidate_id)
    assert score["components"]["automated_tests"] == 40
    assert score["verification_status"] == "tested_in_declared_scope"
    assert "not a calibrated probability of truth" in score["score_scale"]


def test_claim_matrix_exposes_evidence_channels_without_promoting_context_to_truth(tmp_path, monkeypatch):
    from app.knowledge_review import build_claim_verification_matrix

    candidate_id = _candidate(tmp_path, monkeypatch)
    record_review_evidence(
        candidate_id,
        evidence_kind="context",
        source_url="https://example.org/context",
        source_title="Context page",
        excerpt="Photosynthesis converts light energy into chemical energy.",
    )
    record_peer_consultation(
        candidate_id,
        provider="peer-one",
        model_name="review-model",
        prompt="Challenge the lesson.",
        response="The claim may be correct, but I have not checked a primary source.",
        stance="supports",
    )

    matrix = build_claim_verification_matrix(candidate_id)

    assert matrix["candidate_id"] == candidate_id
    assert matrix["verification_channels"]["web_evidence"]["context_only"] == 1
    assert matrix["verification_channels"]["peer_ai"]["counts_as_truth_oracle"] is False
    assert matrix["verification_channels"]["peer_ai"]["providers"] == ["peer-one"]
    assert matrix["claims"]
    assert all(claim["status"] in {
        "unverified", "peer_reviewed_not_verified",
        "source_supported_not_experimentally_verified", "conflicting_evidence",
    } for claim in matrix["claims"])
    assert "does not promote knowledge" in matrix["promotion_policy"]
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
    assert candidate["status"] == "awaiting_test"


def test_unconfigured_peer_ai_attempt_is_persisted_in_candidate_review(tmp_path, monkeypatch):
    from app.knowledge_review import build_claim_verification_matrix

    candidate_id = _candidate(tmp_path, monkeypatch)
    monkeypatch.delenv("PEER_AI_PROVIDERS_JSON", raising=False)

    result = consult_configured_peer_ais(candidate_id)

    assert result["status"] == "unconfigured"
    matrix = build_claim_verification_matrix(candidate_id)
    attempts = matrix["channel_attempts"]
    assert any(
        item["channel"] == "peer_ai"
        and item["status"] == "unconfigured"
        and item["details"]["reason"] == "PEER_AI_PROVIDERS_JSON_not_set"
        for item in attempts
    )


def test_channel_attempt_history_records_web_and_peer_failures_without_secrets(tmp_path, monkeypatch):
    from app.knowledge_review import build_claim_verification_matrix

    candidate_id = _candidate(tmp_path, monkeypatch)

    def broken_discovery(*_args, **_kwargs):
        raise TimeoutError("public search timed out")

    monkeypatch.setattr("app.web_sources.discover_sources", broken_discovery)
    web = research_candidate_online(candidate_id, "photosynthesis")
    assert web["status"] == "unavailable"

    monkeypatch.setenv("PEER_AI_PROVIDERS_JSON", json.dumps([{
        "provider": "peer-test", "model": "review-model",
        "base_url": "https://peer.example/v1", "api_key_env": "NEVER_SET_SECRET",
    }]))
    peer = consult_configured_peer_ais(candidate_id)
    assert peer["status"] == "unavailable"

    matrix = build_claim_verification_matrix(candidate_id)
    attempts = matrix["channel_attempts"]
    assert any(item["channel"] == "web_research" and item["status"] == "unavailable" for item in attempts)
    peer_attempts = [item for item in attempts if item["channel"] == "peer_ai"]
    assert peer_attempts
    assert all("secret" not in json.dumps(item).lower() for item in peer_attempts)


def test_online_research_classifies_claim_linked_sources_only_after_claim_review(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    url = "https://example.org/photosynthesis"
    monkeypatch.setattr(
        "app.web_sources.discover_sources",
        lambda query, limit: [SourceDocument(
            url=url,
            title="Photosynthesis reference",
            text="Photosynthesis converts light energy into chemical energy in plants and algae.",
        )],
    )

    def fake_claim_review(question, answer, sources):
        assert question == "photosynthesis"
        assert url in answer
        assert sources[0]["url"] == url
        assert "chemical energy" in sources[0]["content"]
        return {
            "passed": True,
            "overall": "supported",
            "claims": [{
                "claim": "Photosynthesis converts light energy into chemical energy.",
                "verdict": "supported",
                "evidence_urls": [url],
                "reason": "The retrieved excerpt directly supports the claim.",
            }],
            "source_conflicts": [],
        }

    monkeypatch.setattr("app.claim_review.review_answer_claims", fake_claim_review)
    result = research_candidate_online(candidate_id, "photosynthesis")

    assert result["claim_review"]["passed"] is True
    assert result["claim_review"]["classified_sources"] == 1
    with db.learning_connection() as conn:
        evidence = conn.execute(
            "SELECT evidence_kind, reliability_note FROM knowledge_review_evidence "
            "WHERE candidate_id=? AND source_url=?",
            (candidate_id, url),
        ).fetchone()
    assert evidence["evidence_kind"] == "supporting"
    assert "not independent proof" in evidence["reliability_note"]

def test_evidence_and_peer_agreement_cannot_promote_without_trusted_lab_tests(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    for url in ("https://docs.python.org/3/tutorial/", "https://www.rfc-editor.org/rfc/rfc9110"):
        record_review_evidence(
            candidate_id, evidence_kind="supporting", source_url=url,
            source_title="Primary source", excerpt="Directly supports the bounded candidate claim.",
        )
    record_peer_consultation(candidate_id, provider="peer-a", model_name="m1",
        prompt="Challenge claim", response="Supports with caveats", stance="supports")
    record_peer_consultation(candidate_id, provider="peer-b", model_name="m2",
        prompt="Try to falsify", response="Supports within stated scope", stance="supports")

    result = promote_candidate_after_evidence_review(candidate_id)
    assert result["promoted"] is False
    assert result["reason"] == "lab_verification_required"
    assert result["supporting_domains"] == 2
    assert result["supporting_peer_providers"] == 2
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT status, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0
    assert candidate["status"] == "awaiting_test"
    assert candidate["promoted_knowledge_id"] is None

    record_review_evidence(
        candidate_id, evidence_kind="contradicting",
        source_url="https://www.nist.gov/new-conflicting-guidance",
        excerpt="New source contradicts the previously supported claim.",
    )
    reconsidered = promote_candidate_after_evidence_review(candidate_id)
    assert reconsidered["promoted"] is False
    assert reconsidered["reason"] == "contradicting_evidence_or_failed_field_use"
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT status, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0
    assert candidate["status"] == "awaiting_test"
    assert candidate["promoted_knowledge_id"] is None


def test_evidence_only_review_refuses_known_contradictions_without_promoting(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    record_review_evidence(candidate_id, evidence_kind="supporting",
        source_url="https://docs.python.org/3/tutorial/", excerpt="Supports the bounded claim.")
    record_review_evidence(candidate_id, evidence_kind="supporting",
        source_url="https://www.rfc-editor.org/rfc/rfc9110", excerpt="Supports the bounded claim.")
    record_review_evidence(candidate_id, evidence_kind="contradicting",
        source_url="https://example.gov/contradiction", excerpt="Contradicts the claim.")
    record_peer_consultation(candidate_id, provider="peer-a", model_name="m1",
        prompt="Review", response="Supports", stance="supports")
    record_peer_consultation(candidate_id, provider="peer-b", model_name="m2",
        prompt="Review", response="Supports", stance="supports")
    result = promote_candidate_after_evidence_review(candidate_id)
    assert result["promoted"] is False
    assert result["reason"] == "contradicting_evidence_or_failed_field_use"
    with db.learning_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0



def test_failed_field_feedback_creates_usage_failure_and_downgrades_l2(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        candidate = conn.execute(
            """SELECT job_id, topic, content, source_kind
               FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
        knowledge_id = int(conn.execute(
            """INSERT INTO knowledge_items(
                   topic, content, source_kind, version, status, job_id,
                   trust_level, learning_status, source_url, raw_content
               ) VALUES (?, ?, ?, 1, 'verified', ?, 'L2', 'tested', '', 'source')""",
            (candidate["topic"], candidate["content"], candidate["source_kind"], candidate["job_id"]),
        ).lastrowid)
        conn.execute(
            """UPDATE temporary_knowledge_candidates
               SET status='passed', trust_level='L2', learning_status='tested',
                   promoted_knowledge_id=? WHERE id=?""",
            (knowledge_id, candidate_id),
        )

    record_user_application_feedback(
        candidate_id, outcome="failed",
        application_context="Production use fails when the input is empty.",
        observed_output="IndexError: list index out of range",
        test_steps="Submit an empty input and capture the output.",
    )

    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT status, trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        knowledge = conn.execute(
            "SELECT status, trust_level, learning_status FROM knowledge_items WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        experience = conn.execute(
            """SELECT status, experience_type, problem, test_results_json
               FROM learning_experiences WHERE experience_type='usage_failure'"""
        ).fetchone()
    assert tuple(candidate) == ("failed", "L-1", "failed")
    assert tuple(knowledge) == ("unverified", "L-1", "failed")
    assert experience["status"] == "failed"
    assert experience["experience_type"] == "usage_failure"
    assert "empty input" in experience["problem"]
    assert json.loads(experience["test_results_json"])[0]["counts_as_automated_oracle"] is False
