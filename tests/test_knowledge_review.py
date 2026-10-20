import hashlib
import json

import pytest

from app import db, learning
from app.knowledge_review import (
    get_candidate_review_summary,
    record_peer_consultation,
    record_review_evidence,
    research_candidate_online,
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
    assert summary["verification_status"] == "unverified"
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
