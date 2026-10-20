import pytest

from app.learning_rubric import score_with_rubric, validate_rubric


def _rubric():
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


def test_rubric_scoring_is_deterministic_and_partial():
    result = score_with_rubric(
        ["Faster lookup; maintenance cost.", "Isolation level prevents a dirty read."],
        _rubric(),
    )
    assert result["score"] == 100.0
    assert result["correct_count"] == 2
    assert result["scoring_method"] == "rubric_concept_coverage_v1"
    assert result["independent_verification"] is False


def test_rubric_scoring_gives_partial_credit_for_missing_concepts():
    result = score_with_rubric(
        ["Faster lookup.", "Isolation level."],
        _rubric(),
    )
    assert result["score"] == 60.0
    assert result["per_question"][0]["score"] == 66.67
    assert result["per_question"][1]["score"] == 50.0


def test_rubric_rejects_bad_shapes_and_unbounded_variants():
    assert validate_rubric([{"criteria": []}], 1) is False
    bad = _rubric()
    bad[0]["criteria"][0]["accepted_phrases"] = ["x"]
    assert validate_rubric(bad, 2) is False
    with pytest.raises(ValueError):
        score_with_rubric(["answer"], _rubric())


def test_normalization_handles_persian_arabic_character_variants():
    from app.learning_rubric import normalize_concept_text

    assert normalize_concept_text("يک كليد") == normalize_concept_text("یک کلید")
