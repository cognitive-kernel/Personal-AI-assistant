import pytest

from app.learning import _adaptive_difficulty_guidance


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
