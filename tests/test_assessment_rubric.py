import pytest

from app.assessment_rubric import normalize_rubric, score_answers


def _rubric():
    return normalize_rubric([
        {"required_concepts": [
            {"concept": "query plan", "accepted_phrases": ["execution plan", "query plan"]},
            {"concept": "index maintenance", "accepted_phrases": ["index maintenance", "maintaining indexes"]},
        ]},
        {"required_concepts": [
            {"concept": "source provenance", "accepted_phrases": ["source provenance", "source origin"]},
            {"concept": "uncertainty", "accepted_phrases": ["state uncertainty", "uncertain"]},
        ]},
    ], 2)


def test_rubric_scores_paraphrases_and_exposes_missing_concepts():
    rubric = _rubric()
    result = score_answers([
        "The execution plan shows how the query runs and maintaining indexes has a cost.",
        "The source origin should be preserved; state uncertainty when evidence is missing.",
    ], rubric)
    assert result["score"] == 100.0
    assert result["correct_count"] == 2
    assert result["scoring_method"] == "rubric_concept_coverage_v1"
    assert result["independent_verification"] is False


def test_rubric_gives_partial_credit_instead_of_exact_match_zero():
    result = score_answers(
        ["The execution plan explains query behavior.", "Uncertain."],
        _rubric(),
    )
    assert 0 < result["score"] < 100
    assert result["question_results"][0]["missing_concepts"] == ["index maintenance"]
    assert result["correct_count"] == 1


@pytest.mark.parametrize("value", [None, {}, [], ["invalid"]])
def test_invalid_rubric_fails_closed(value):
    assert normalize_rubric(value, 1) is None


def test_rubric_rejects_wrong_question_count_and_malformed_phrases():
    assert normalize_rubric(_rubric(), 3) is None
    assert normalize_rubric([{"required_concepts": [{"concept": "valid", "accepted_phrases": [42]}]}], 1) is None
