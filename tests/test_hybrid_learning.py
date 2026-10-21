import hashlib
import json

from app import db, learning
from app import hybrid_learning
from app.knowledge_review import build_claim_verification_matrix, record_peer_consultation


def _candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("hybrid candidate", source_text="Source text retained for later tests.")
    lesson = "A concise candidate that remains temporary until trusted tests pass."
    digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        return int(conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["topic"], lesson, "user_provided_text",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid)


def test_hybrid_verification_attempts_each_channel_after_other_channels_fail(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    # CI may disable automatic web research globally; this test explicitly exercises
    # a configured web channel whose provider then fails.
    monkeypatch.setenv("LEARNING_AUTO_WEB_RESEARCH", "true")
    attempted = []

    def record_attempt(candidate_id, *, channel, status, provider="", details=None):
        attempted.append((channel, status, details or {}))
        return {}

    monkeypatch.setattr("app.knowledge_review.record_review_attempt", record_attempt)
    monkeypatch.setattr("app.knowledge_review.research_candidate_online",
                        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr("app.knowledge_review.consult_configured_peer_ais",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr("app.learning_verification.run_candidate_source_doctests",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("app.knowledge_review.get_candidate_review_summary",
                        lambda _candidate_id: {"verification_status": "unverified"})
    monkeypatch.setattr("app.knowledge_review.build_claim_verification_matrix",
                        lambda _candidate_id: {"claims": [], "promotion_performed": False})

    result = hybrid_learning.run_hybrid_verification(candidate_id)

    assert set(result["channels"]) == {"web_research", "peer_ai", "sandbox"}
    assert {key: value["status"] for key, value in result["channels"].items()} == {
        "web_research": "failed", "peer_ai": "failed", "sandbox": "failed",
    }
    assert {item[0] for item in attempted} == {"web_research", "peer_ai", "sandbox"}
    assert result["promotion_performed"] is False
    with db.learning_connection() as conn:
        status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
    assert status == "awaiting_test"


def test_failed_explicit_url_skips_search_but_still_consults_other_channels(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    attempted = []
    calls = {"peer": 0, "sandbox": 0, "search": 0}

    monkeypatch.setattr("app.knowledge_review.record_review_attempt",
        lambda cid, *, channel, status, provider="", details=None: attempted.append((channel, status, details or {})) or {})
    monkeypatch.setattr("app.knowledge_review.research_candidate_online",
        lambda *args, **kwargs: calls.__setitem__("search", calls["search"] + 1))
    monkeypatch.setattr("app.knowledge_review.consult_configured_peer_ais",
        lambda *args, **kwargs: calls.__setitem__("peer", calls["peer"] + 1) or {"status": "unconfigured"})
    monkeypatch.setattr("app.learning_verification.run_candidate_source_doctests",
        lambda *args, **kwargs: calls.__setitem__("sandbox", calls["sandbox"] + 1) or {"status": "no_examples"})
    monkeypatch.setattr("app.knowledge_review.get_candidate_review_summary",
        lambda _candidate_id: {"verification_status": "unverified"})
    monkeypatch.setattr("app.knowledge_review.build_claim_verification_matrix",
        lambda _candidate_id: {"promotion_performed": False})

    result = hybrid_learning.run_hybrid_verification(candidate_id, web_mode="skip_requested_url_failed")

    assert calls == {"peer": 1, "sandbox": 1, "search": 0}
    assert result["channels"]["web_research"]["status"] == "unavailable"
    assert result["channels"]["peer_ai"]["status"] == "unconfigured"
    assert any(channel == "web_research" and status == "unavailable" for channel, status, _ in attempted)
    assert result["promotion_performed"] is False


def test_disabled_web_channel_does_not_disable_peer_or_sandbox(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    calls = {"peer": 0, "sandbox": 0}
    monkeypatch.setenv("LEARNING_AUTO_WEB_RESEARCH", "false")
    monkeypatch.setattr("app.knowledge_review.record_review_attempt", lambda *args, **kwargs: {})
    monkeypatch.setattr("app.knowledge_review.consult_configured_peer_ais",
        lambda *args, **kwargs: calls.__setitem__("peer", calls["peer"] + 1) or {"status": "unconfigured"})
    monkeypatch.setattr("app.learning_verification.run_candidate_source_doctests",
        lambda *args, **kwargs: calls.__setitem__("sandbox", calls["sandbox"] + 1) or {"status": "no_examples"})
    monkeypatch.setattr("app.knowledge_review.get_candidate_review_summary",
        lambda _candidate_id: {"verification_status": "unverified"})
    monkeypatch.setattr("app.knowledge_review.build_claim_verification_matrix",
        lambda _candidate_id: {"promotion_performed": False})

    result = hybrid_learning.run_hybrid_verification(candidate_id)

    assert result["channels"]["web_research"]["status"] == "unconfigured"
    assert calls == {"peer": 1, "sandbox": 1}

def test_peer_test_suggestions_are_visible_but_never_treated_as_trusted_oracles(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    response = json.dumps({
        "stance": "supports",
        "testable_tests": ["Check the edge case where input is empty."],
        "applications": ["Use this in a small data-cleaning task."],
        "limitations": ["This result may not generalize beyond the stated input range."],
    })
    record_peer_consultation(
        candidate_id, provider="peer-one", model_name="review-model",
        prompt="Challenge this lesson and propose tests.", response=response, stance="supports",
    )

    matrix = build_claim_verification_matrix(candidate_id)
    peer_channel = matrix["verification_channels"]["peer_ai"]

    assert peer_channel["suggested_tests"][0]["proposal"] == "Check the edge case where input is empty."
    assert peer_channel["suggested_tests"][0]["executed"] is False
    assert peer_channel["suggested_tests"][0]["trusted_oracle"] is False
    assert peer_channel["suggested_tests"][0]["status"] == "exploratory_only"
    assert peer_channel["suggested_applications"]
    assert peer_channel["suggested_limitations"]
    assert peer_channel["counts_as_truth_oracle"] is False
