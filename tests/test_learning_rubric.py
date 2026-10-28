import json
from pathlib import Path

import pytest

from app.learning_rubric import normalize_concept_text, score_with_rubric, validate_rubric


def rubric():
    return [
        {"criteria": [
            {"id": "definition", "weight": 2, "accepted_phrases": ["index improves lookup", "faster lookup"]},
            {"id": "tradeoff", "weight": 1, "accepted_phrases": ["maintenance cost", "cost of maintaining"]},
        ]},
        {"criteria": [
            {"id": "cause", "weight": 1, "accepted_phrases": ["transaction isolation", "isolation level"]},
            {"id": "effect", "weight": 1, "accepted_phrases": ["dirty read", "uncommitted data"]},
        ]},
    ]


def test_rubric_scoring_is_deterministic_and_weighted():
    answers = ["Faster lookup; maintenance cost.", "Isolation level prevents a dirty read."]
    first = score_with_rubric(answers, rubric())
    second = score_with_rubric(answers, rubric())
    assert first == second
    assert first["score"] == 100.0
    assert first["correct_count"] == 2
    assert first["independent_verification"] is False


def test_rubric_gives_partial_credit_for_missing_concepts():
    result = score_with_rubric(
        ["Faster lookup.", "Isolation level."],
        rubric(),
    )
    assert result["score"] == 60.0
    assert result["correct_count"] == 0
    assert result["per_question"][0]["matched_criteria"] == 1


def test_rubric_rejects_unbounded_or_malformed_shapes():
    assert not validate_rubric([], 0)
    assert not validate_rubric([{"criteria": []}], 1)
    assert not validate_rubric([{"criteria": [{"id": "x", "weight": True, "accepted_phrases": ["some phrase"]}, {"id": "y", "weight": 1, "accepted_phrases": ["another phrase"]}]}], 1)
    with pytest.raises(ValueError, match="invalid"):
        score_with_rubric(["answer"], [{"criteria": []}])


def test_normalization_handles_persian_arabic_variants():
    assert normalize_concept_text("يادگيری كد") == normalize_concept_text("یادگیری کد")


def test_negated_concept_does_not_receive_credit():
    result = score_with_rubric(["not faster lookup; maintenance cost.", "Isolation level prevents a dirty read."], rubric())
    assert result["per_question"][0]["criteria"][0]["matched"] is False


def test_calibration_fixture_has_no_false_acceptance():
    path = Path(__file__).resolve().parent / "fixtures" / "learning_rubric_calibration.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    threshold = float(fixture["pass_threshold"])
    for case in fixture["cases"]:
        result = score_with_rubric(case["answers"], fixture["rubric"])
        assert (result["score"] >= threshold) is case["expected_pass"], case["id"]
