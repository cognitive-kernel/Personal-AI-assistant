import hashlib
import json
from pathlib import Path

import pytest

from app import factual_semantic_review
from app.factual_semantic_review import RUBRIC_VERSION, adjudicate_assessment


_FIXTURE = Path(__file__).parent / "fixtures" / "factual_semantic_review_golden.json"


@pytest.mark.parametrize(
    "case",
    json.loads(_FIXTURE.read_text(encoding="utf-8")),
    ids=lambda case: case["id"],
)
def test_golden_semantic_review_policy(case):
    decision = adjudicate_assessment(case["assessment"])
    assert decision.accepted is case["expected"]["accepted"]
    assert decision.score == case["expected"]["score"]
    assert decision.rubric_version == RUBRIC_VERSION
    assert decision.reasons


def test_missing_review_dimension_fails_closed():
    with pytest.raises(ValueError, match="missing boolean rubric checks"):
        adjudicate_assessment({"verdict": "supports", "claim_atomic": True})


def test_unknown_verdict_is_rejected():
    with pytest.raises(ValueError, match="verdict must be"):
        adjudicate_assessment({"verdict": "probably_true"})



def _reviewer_response(**overrides):
    value = {
        "verdict": "supports",
        "claim_atomic": True,
        "source_directly_addresses_claim": True,
        "scope_matches": True,
        "source_version_matches_claim": True,
        "runtime_environment_matches": True,
        "source_is_authoritative": True,
        "no_unresolved_conflict": True,
        "test_directly_checks_claim": True,
        "rationale": "The source directly describes the claimed behavior and the test exercises it.",
        "confidence": 0.95,
    }
    value.update(overrides)
    return json.dumps(value)


def _mock_independent_model(monkeypatch, response):
    monkeypatch.setattr(
        factual_semantic_review,
        "get_settings",
        lambda: {
            "ollama_model": "generator:7b",
            "learning_ollama_model": "generator:7b",
            "factual_review_ollama_model": "reviewer:7b",
        },
    )
    monkeypatch.setattr(
        factual_semantic_review.ollama,
        "list_models",
        lambda: [
            {"name": "generator:7b", "digest": "a" * 64},
            {"name": "reviewer:7b", "digest": "b" * 64},
        ],
    )
    captured = {}
    def fake_chat(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return response
    monkeypatch.setattr(factual_semantic_review.ollama, "chat", fake_chat)
    return captured


def test_independent_model_review_accepts_only_distinct_model_with_valid_response(monkeypatch):
    captured = _mock_independent_model(monkeypatch, _reviewer_response())
    decision = factual_semantic_review.review_claim_against_source(
        claim="An assertion raises AssertionError when false.",
        claim_hash="c" * 64,
        source_excerpt="The assert statement raises AssertionError when the expression is false.",
        source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
        source_hash=hashlib.sha256(b"The assert statement raises AssertionError when the expression is false.").hexdigest(),
        source_version="3",
        claim_version="3.12.3",
        environment_name="python",
        environment_version="3.12.3",
        test_source="try:\\n    assert False\\nexcept AssertionError:\\n    pass",
    )
    assert decision.accepted is True
    assert decision.evaluator_model == "reviewer:7b"
    assert decision.evaluator_digest == "b" * 64
    assert captured["purpose"] == "factual_review"


def test_independent_model_review_fails_closed_on_source_version_mismatch(monkeypatch):
    _mock_independent_model(monkeypatch, _reviewer_response())
    decision = factual_semantic_review.review_claim_against_source(
        claim="An assertion raises AssertionError when false.",
        claim_hash="c" * 64,
        source_excerpt="The assert statement raises AssertionError when the expression is false.",
        source_url="https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement",
        source_hash=hashlib.sha256(b"The assert statement raises AssertionError when the expression is false.").hexdigest(),
        source_version="3.11",
        claim_version="3.12.3",
        environment_name="python",
        environment_version="3.12.3",
        test_source="assert False",
    )
    assert decision.accepted is False
    assert "source_version_matches_claim" in decision.reasons[0]


def test_same_model_digest_cannot_be_used_as_independent_reviewer(monkeypatch):
    _mock_independent_model(monkeypatch, _reviewer_response())
    monkeypatch.setattr(
        factual_semantic_review.ollama,
        "list_models",
        lambda: [
            {"name": "generator:7b", "digest": "a" * 64},
            {"name": "reviewer:7b", "digest": "a" * 64},
        ],
    )
    with pytest.raises(ValueError, match="digest must differ"):
        factual_semantic_review.review_claim_against_source(
            claim="A claim",
            claim_hash="c" * 64,
            source_excerpt="Source excerpt",
            source_url="https://example.org/source",
            source_hash=hashlib.sha256(b"Source excerpt").hexdigest(),
            source_version="3",
            claim_version="3.12.3",
            environment_name="python",
            environment_version="3.12.3",
            test_source="assert True",
        )
