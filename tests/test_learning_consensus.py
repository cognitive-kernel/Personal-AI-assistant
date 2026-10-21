import hashlib
import json

from app import db, learning
from app.knowledge_review import record_peer_consultation
from app.learning_consensus import evaluate_candidate_consensus, latest_candidate_consensus


def _candidate(tmp_path, monkeypatch, *, source=True):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job(
        "consensus fixture",
        source_text="Original technical source text with enough context." if source else None,
    )
    lesson = "A specific technical claim with conditions and a testable outcome."
    digest = hashlib.sha256(lesson.encode()).hexdigest()
    with db.learning_connection() as conn:
        candidate_id = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["topic"], lesson,
             "user_provided_text" if source else "model_knowledge_no_external_source",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid
    return int(candidate_id)


def _vote(candidate_id, provider, model, stance, response):
    return record_peer_consultation(
        candidate_id, provider=provider, model_name=model,
        prompt="Learning-consensus-v1: vote on one testable claim.",
        response=response, stance=stance,
    )


def test_two_distinct_supporting_models_with_original_source_reach_l1(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "Supports: claim applies within stated conditions.")
    _vote(candidate_id, "peer-b", "large-model-b", "supports", "Supports: independent critique is positive.")

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "model_consensus"
    assert result["trust_level"] == "L1"
    assert result["supporting_count"] == 2
    assert result["consensus_is_not_proof_of_truth"] is True
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT trust_level, learning_status, status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert tuple(candidate) == ("L1", "model_consensus", "awaiting_test")
    assert latest_candidate_consensus(candidate_id)["votes"][0]["response_excerpt"]


def test_one_model_repeated_cannot_satisfy_minimum_independent_consensus(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "First response.")
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "Second response.")

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "insufficient_evidence"
    assert result["trust_level"] == "L0"
    assert result["supporting_count"] == 1
    assert result["decision_reason"] == "minimum_distinct_supporting_models_not_reached"


def test_model_disagreement_sets_l_minus_one_and_records_failure_experience(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "Supported only with a narrow condition.")
    _vote(candidate_id, "peer-b", "large-model-b", "contradicts", "The claim is false in the documented case.")

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "conflicting"
    assert result["trust_level"] == "L-1"
    assert result["learning_status"] == "conflicting"
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT trust_level, learning_status FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        experience = conn.execute(
            "SELECT status, problem FROM learning_experiences WHERE topic='consensus fixture' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert tuple(candidate) == ("L-1", "conflicting")
    assert experience["status"] == "failed"
    assert str(candidate_id) in experience["problem"]


def test_legacy_peer_prompt_votes_do_not_count_in_current_consensus(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "Current prompt vote.")
    record_peer_consultation(
        candidate_id, provider="peer-b", model_name="large-model-b",
        prompt="Legacy unversioned prompt.", response="Supports an older version of the claim.",
        stance="supports", prompt_version="learning-peer-review-v1",
    )

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "insufficient_evidence"
    assert result["supporting_count"] == 1
    assert result["prompt_version"] == "learning-peer-review-v2"
    assert all(vote["prompt_version"] == "learning-peer-review-v2" for vote in result["votes"])


def test_consensus_requires_a_recorded_original_source(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch, source=False)
    _vote(candidate_id, "peer-a", "large-model-a", "supports", "Supports.")
    _vote(candidate_id, "peer-b", "large-model-b", "supports", "Supports.")

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "insufficient_evidence"
    assert result["source_present"] is False
    assert result["trust_level"] == "L0"



def test_different_provider_aliases_for_same_model_do_not_count_as_independent(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    _vote(candidate_id, "peer-a", "same-model:8b", "supports", "Support from first endpoint.")
    _vote(candidate_id, "peer-b", "same-model:8b", "supports", "Support from provider alias.")

    result = evaluate_candidate_consensus(candidate_id)

    assert result["outcome"] == "insufficient_evidence"
    assert result["supporting_count"] == 1
    assert result["trust_level"] == "L0"
    assert result["decision_reason"] == "minimum_distinct_supporting_models_not_reached"
