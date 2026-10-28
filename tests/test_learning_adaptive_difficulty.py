import pytest

from app.learning import _adaptive_difficulty_guidance, _semantic_gap_difficulty_guidance


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (None, "baseline"),
        (float("nan"), "baseline"),
        (-1, "baseline"),
        (101, "baseline"),
        (0, "foundational"),
        (49.99, "foundational"),
        (50, "reinforcement"),
        (79.99, "reinforcement"),
        (80, "application"),
        (89.99, "application"),
        (90, "advanced_transfer"),
        (100, "advanced_transfer"),
    ],
)
def test_adaptive_difficulty_uses_bounded_score_bands(score, level):
    result = _adaptive_difficulty_guidance(score)

    assert result["level"] == level
    assert result["instruction"].strip()
    assert len(result["instruction"]) <= 300


def test_adaptive_difficulty_does_not_treat_high_score_as_verified_mastery():
    result = _adaptive_difficulty_guidance(98)

    assert result["level"] == "advanced_transfer"
    assert "transfer" in result["instruction"]



def test_semantic_gap_guidance_targets_missing_concepts_without_claiming_verification():
    review = {
        "independent_evaluator": True,
        "independent_verification": False,
        "evaluator_digest": "distinct-model-digest",
        "review": {
            "items": [
                {"judgment": "partial", "missing_concepts": ["transaction isolation"]},
                {"judgment": "incorrect", "missing_concepts": ["locking behavior"]},
            ]
        },
    }

    result = _semantic_gap_difficulty_guidance(__import__("json").dumps(review))

    assert result is not None
    assert result["level"] == "semantic_reinforcement"
    assert "transaction isolation" in result["instruction"]
    assert "Do not treat this model review as a factual verdict" in result["instruction"]


def test_semantic_gap_guidance_ignores_untrusted_or_invalid_reviews():
    assert _semantic_gap_difficulty_guidance(None) is None
    assert _semantic_gap_difficulty_guidance('{"review": {"items": []}}') is None
