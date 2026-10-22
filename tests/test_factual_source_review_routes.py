import pytest
from fastapi import HTTPException

from app import db, factual_knowledge, factual_verification_routes


@pytest.fixture
def review_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _claim_with_source(*, risk_level="low", source_ref="https://docs.python.org/3/library/stdtypes.html"):
    with db.learning_connection() as connection:
        knowledge_id = factual_knowledge.create_claim(
            connection,
            "Python lists are mutable sequences.",
            environment_name="python",
            environment_version="3.12.3",
            scope={"topic": "Python list mutability"},
            risk_level=risk_level,
        )
        factual_knowledge.add_evidence(
            connection,
            knowledge_id,
            source_type="official_docs",
            source_ref=source_ref,
            source_hash="a" * 64,
            excerpt="Python lists are mutable sequences.",
        )
    return knowledge_id


def test_source_review_queue_lists_only_unreviewed_claims_with_evidence(review_store):
    knowledge_id = _claim_with_source()
    response = factual_verification_routes.list_factual_source_review_queue(limit=10)
    assert response["count"] == 1
    item = response["items"][0]
    assert item["id"] == knowledge_id
    assert item["evidence_level"] == "L0_raw"
    assert item["lifecycle_status"] == "unverified"
    assert item["evidence"][0]["source_ref"].startswith("https://")


def test_explicit_human_source_review_promotes_only_to_l1_and_audits(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=True,
        review_note="Compared the exact claim with the cited Python documentation.",
    )
    result = factual_verification_routes.approve_factual_source(knowledge_id, request)
    assert result["evidence_level"] == "L1_sourced"
    assert result["lifecycle_status"] == "active"
    assert "not proof from an independent test" in result["message"]

    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        audit = connection.execute(
            """SELECT reviewer, action, evidence_level_before, evidence_level_after,
                      lifecycle_before, lifecycle_after, source_hashes_json, review_note
               FROM factual_knowledge_manual_reviews WHERE knowledge_id=?""",
            (knowledge_id,),
        ).fetchone()
    assert tuple(claim) == ("L1_sourced", "active")
    assert audit["reviewer"] == "local_user"
    assert audit["action"] == "source_approved"
    assert audit["evidence_level_before"] == "L0_raw"
    assert audit["evidence_level_after"] == "L1_sourced"
    assert audit["lifecycle_before"] == "unverified"
    assert audit["lifecycle_after"] == "active"
    assert audit["source_hashes_json"] == '["' + "a" * 64 + '"]'
    assert "Compared the exact claim" in audit["review_note"]


def test_high_risk_source_review_never_activates_claim(review_store):
    knowledge_id = _claim_with_source(risk_level="high")
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=True,
        review_note="Checked the cited source but the claim remains high risk.",
    )
    result = factual_verification_routes.approve_factual_source(knowledge_id, request)
    assert result["evidence_level"] == "L1_sourced"
    assert result["lifecycle_status"] == "needs_review"


def test_source_review_requires_explicit_confirmation(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=False,
        source_supports_claim=True,
        review_note="Compared the exact claim with the cited Python documentation.",
    )
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.approve_factual_source(knowledge_id, request)
    assert raised.value.status_code == 422
    with db.learning_connection() as connection:
        level = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()[0]
    assert level == "L0_raw"


def test_source_review_rejects_untrusted_or_non_https_source(review_store):
    knowledge_id = _claim_with_source(source_ref="http://docs.python.org/3/library/stdtypes.html")
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=True,
        review_note="Reviewed the citation, but this source is not using HTTPS.",
    )
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.approve_factual_source(knowledge_id, request)
    assert raised.value.status_code == 409
    with db.learning_connection() as connection:
        level = connection.execute(
            "SELECT evidence_level FROM factual_knowledge WHERE id=?", (knowledge_id,)
        ).fetchone()[0]
    assert level == "L0_raw"


def test_source_review_is_not_repeatable_after_promotion(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=True,
        review_note="Compared the exact claim with the cited Python documentation.",
    )
    factual_verification_routes.approve_factual_source(knowledge_id, request)
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.approve_factual_source(knowledge_id, request)
    assert raised.value.status_code == 409


def test_explicit_source_rejection_quarantines_without_claiming_false(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=False,
        review_note="The excerpt does not establish the exact claim and needs a better source.",
    )
    result = factual_verification_routes.reject_factual_source(knowledge_id, request)
    assert result["evidence_level"] == "L0_raw"
    assert result["lifecycle_status"] == "needs_review"
    assert "does not establish that the claim is false" in result["message"]

    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        audit = connection.execute(
            """SELECT action, evidence_level_before, evidence_level_after,
                      lifecycle_before, lifecycle_after, source_hashes_json, review_note
               FROM factual_knowledge_manual_reviews WHERE knowledge_id=?""",
            (knowledge_id,),
        ).fetchone()
    assert tuple(claim) == ("L0_raw", "needs_review")
    assert audit["action"] == "source_rejected"
    assert audit["evidence_level_before"] == audit["evidence_level_after"] == "L0_raw"
    assert audit["lifecycle_before"] == "unverified"
    assert audit["lifecycle_after"] == "needs_review"
    assert audit["source_hashes_json"] == '["' + "a" * 64 + '"]'
    assert "does not establish" in audit["review_note"]


def test_source_rejection_requires_explicit_confirmation_and_note(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=False,
        source_supports_claim=False,
        review_note="The source is insufficient to support this claim.",
    )
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.reject_factual_source(knowledge_id, request)
    assert raised.value.status_code == 422
    with db.learning_connection() as connection:
        claim = connection.execute(
            "SELECT evidence_level, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
    assert tuple(claim) == ("L0_raw", "unverified")


def test_source_rejection_cannot_repeat_after_quarantine(review_store):
    knowledge_id = _claim_with_source()
    request = factual_verification_routes.FactualSourceReviewRequest(
        confirm=True,
        source_supports_claim=False,
        review_note="The source does not support this exact claim sufficiently.",
    )
    factual_verification_routes.reject_factual_source(knowledge_id, request)
    with pytest.raises(HTTPException) as raised:
        factual_verification_routes.reject_factual_source(knowledge_id, request)
    assert raised.value.status_code == 409
